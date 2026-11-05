/*
 * Copyright (c) 2025 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#define USING_LOG_PREFIX SQL_RESV
#include "sql/resolver/ddl/ob_explain_resolver.h"
#include "sql/resolver/ob_resolver.h"
#include "sql/resolver/expr/ob_raw_expr_util.h"
#include "share/file_sql/ob_file_scan_utils.h"

using namespace oceanbase;
using namespace sql;
using namespace common;
const static int64_t EXPLAIN_FORMAT = 0;
const static int64_t EXPLAIN_DISPLAY_OPTION = 1;
const static int64_t EXPLAIN_INTO_TABLE = 2;
const static int64_t EXPLAIN_STMT_ID = 3;
const static int64_t EXPLAIN_CHILD_STMT = 4;

namespace oceanbase
{
namespace sql
{
// DuckDB-style DESCRIBE of a file table: rewrite
//   describe select ... from read_csv(...)
// into a constant UNION ALL select that renders the inferred schema in the
// SHOW COLUMNS format (column_name / column_type / null / key / default /
// extra). null = YES iff the inferred column contains NULL values.
static const char *file_col_type_str(const common::ObObjMeta &meta)
{
  const char *ret = "VARCHAR";
  switch (meta.get_type()) {
    case ObTinyIntType:
      ret = "BOOLEAN";
      break;
    case ObIntType:
      ret = "BIGINT";
      break;
    case ObDoubleType:
      ret = "DOUBLE";
      break;
    case ObDateType:
      ret = "DATE";
      break;
    case ObDateTimeType:
      ret = "DATETIME";
      break;
    default:
      ret = "VARCHAR";
      break;
  }
  return ret;
}

static int add_describe_const_item(ObRawExprFactory &expr_factory,
                                   ObSelectStmt *sel,
                                   ObRawExpr *expr,
                                   const char *alias)
{
  int ret = OB_SUCCESS;
  SelectItem si;
  si.expr_ = expr;
  si.is_real_alias_ = true;
  si.alias_name_.assign_ptr(const_cast<char *>(alias), static_cast<int32_t>(strlen(alias)));
  if (OB_FAIL(expr->add_flag(IS_ALIAS))) {
    LOG_WARN("fail to set alias flag", K(ret));
  } else if (OB_FAIL(sel->add_select_item(si))) {
    LOG_WARN("fail to add select item", K(ret));
  }
  return ret;
}

static int build_file_describe_child_stmt(ObExplainResolver &resolver,
                                          ObRawExprFactory &expr_factory,
                                          const char *col_name,
                                          const char *col_type,
                                          const char *null_str,
                                          ObSelectStmt *&out)
{
  int ret = OB_SUCCESS;
  ObSelectStmt *sel = NULL;
  ObConstRawExpr *ce = NULL;
  ObRawExpr *e = NULL;
  if (OB_ISNULL(sel = resolver.create_stmt<ObSelectStmt>())) {
    ret = OB_ERR_UNEXPECTED;
    LOG_WARN("fail to create describe select stmt", K(ret));
  }
  // column_name
  if (OB_FAIL(ret)) {
  } else if (OB_FAIL(ObRawExprUtils::build_const_string_expr(
                   expr_factory, ObVarcharType, ObString(strlen(col_name), col_name),
                   CS_TYPE_UTF8MB4_GENERAL_CI, ce))) {
    LOG_WARN("fail to build const expr", K(ret));
  } else {
    e = ce;
    if (OB_FAIL(add_describe_const_item(expr_factory, sel, e, "column_name"))) {
      LOG_WARN("fail to add item", K(ret));
    }
  }
  // column_type
  if (OB_FAIL(ret)) {
  } else if (OB_FAIL(ObRawExprUtils::build_const_string_expr(
                   expr_factory, ObVarcharType, ObString(strlen(col_type), col_type),
                   CS_TYPE_UTF8MB4_GENERAL_CI, ce))) {
  } else {
    e = ce;
    if (OB_FAIL(add_describe_const_item(expr_factory, sel, e, "column_type"))) {
      LOG_WARN("fail to add item", K(ret));
    }
  }
  // null
  if (OB_FAIL(ret)) {
  } else if (OB_FAIL(ObRawExprUtils::build_const_string_expr(
                   expr_factory, ObVarcharType, ObString(strlen(null_str), null_str),
                   CS_TYPE_UTF8MB4_GENERAL_CI, ce))) {
  } else {
    e = ce;
    if (OB_FAIL(add_describe_const_item(expr_factory, sel, e, "null"))) {
      LOG_WARN("fail to add item", K(ret));
    }
  }
  // key / default / extra are NULL
  if (OB_FAIL(ret)) {
  } else if (OB_FAIL(ObRawExprUtils::build_null_expr(expr_factory, e))) {
    LOG_WARN("fail to build null expr", K(ret));
  } else if (OB_FAIL(add_describe_const_item(expr_factory, sel, e, "key"))) {
    LOG_WARN("fail to add item", K(ret));
  }
  if (OB_FAIL(ret)) {
  } else if (OB_FAIL(ObRawExprUtils::build_null_expr(expr_factory, e))) {
  } else if (OB_FAIL(add_describe_const_item(expr_factory, sel, e, "default"))) {
    LOG_WARN("fail to add item", K(ret));
  }
  if (OB_FAIL(ret)) {
  } else if (OB_FAIL(ObRawExprUtils::build_null_expr(expr_factory, e))) {
  } else if (OB_FAIL(add_describe_const_item(expr_factory, sel, e, "extra"))) {
    LOG_WARN("fail to add item", K(ret));
  }
  if (OB_SUCC(ret)) {
    out = sel;
  }
  return ret;
}

static bool get_file_table_marker(const ObSelectStmt *sel, ObString &marker)
{
  bool is_file = false;
  if (NULL != sel && 1 == sel->get_table_size()) {
    const TableItem *ti = sel->get_table_item(0);
    if (NULL != ti && ti->is_function_table() && NULL != ti->function_table_expr_
        && ti->function_table_expr_->is_const_expr()) {
      const ObConstRawExpr *ce = static_cast<const ObConstRawExpr *>(ti->function_table_expr_);
      if (ce->get_value().is_varchar()) {
        marker = ce->get_value().get_varchar();
        is_file = file_sql::is_file_table_marker(marker.ptr(), marker.length());
      }
    }
  }
  return is_file;
}
} // end namespace sql
} // end namespace oceanbase

int ObExplainResolver::resolve(const ParseNode &parse_tree)
{
  int ret = OB_SUCCESS;
  ObExplainStmt *explain_stmt = NULL;
  if (!(T_EXPLAIN == parse_tree.type_  && 5 == parse_tree.num_child_)) {
    ret = OB_ERR_UNEXPECTED;
    LOG_WARN("invalid EXPLAIN syntax",
             "type", parse_tree.type_,
             "# of child", parse_tree.num_child_);
  } else if (NULL == (explain_stmt = create_stmt<ObExplainStmt>())) {
    ret = OB_SQL_RESOLVER_NO_MEMORY;
    LOG_WARN("failed to create explain stmt");
  } else {
    ParseNode *child_node = parse_tree.children_[EXPLAIN_CHILD_STMT];
    ObStmt *child_stmt = NULL;
    ObExplainDisplayOpt opt;
    opt.with_tree_line_ = true;
    if (OB_ISNULL(child_node)) {
      ret = OB_ERR_UNEXPECTED;
      LOG_WARN("empty parse tree for stmt");
    } else if (!(T_SELECT == child_node->type_
                 || T_DELETE == child_node->type_
                 || T_INSERT == child_node->type_
                 || T_UPDATE == child_node->type_
                 || T_REPLACE == child_node->type_)) {
      ret = OB_NOT_SUPPORTED;
      LOG_WARN("invalid stmt type for EXPLAIN", "type", child_node->type_);
      LOG_USER_ERROR(OB_NOT_SUPPORTED, "stmt type for EXPLAIN");
    } else {
      ObResolver resolver(params_);
      if (OB_FAIL(resolver.resolve(ObResolver::IS_NOT_PREPARED_STMT,
                                   *child_node,
                                   child_stmt))) {
      } else if (OB_ISNULL(child_stmt)) {
        ret = OB_ERR_UNEXPECTED;
        LOG_WARN("failed to generate stmt for child stmt");
      } else if (T_SELECT == child_node->type_) {
        // file-table DESCRIBE: describe select ... from read_csv(...) etc.
        ObSelectStmt *child_sel = static_cast<ObSelectStmt *>(child_stmt);
        ObString marker;
        if (!get_file_table_marker(child_sel, marker)) {
          explain_stmt->set_explain_query_stmt(static_cast<ObDMLStmt *>(child_stmt));
        } else {
          file_sql::ObFileTableParam param;
          if (OB_FAIL(file_sql::decode_file_table_param(marker.ptr(), marker.length(), param))) {
            LOG_WARN("fail to decode file table marker", K(ret));
          } else {
            ObSEArray<file_sql::ObFileTableCol, 16> cols;
            if (OB_FAIL(file_sql::infer_file_schema(param, *params_.allocator_, cols))) {
              LOG_WARN("fail to infer file schema for describe", K(ret), K(param.path_));
            } else {
              ObSelectStmt *first = NULL;
              ObSelectStmt *top = NULL;
              for (int64_t i = 0; OB_SUCC(ret) && i < cols.count(); ++i) {
                ObSelectStmt *child = NULL;
                file_sql::ObFileTableCol &c = cols.at(i);
                char type_buf[32];
                snprintf(type_buf, sizeof(type_buf), "%s",
                         file_col_type_str(c.meta_));
                if (OB_FAIL(build_file_describe_child_stmt(
                        *this, *params_.expr_factory_, c.name_.ptr(),
                        type_buf, c.has_null_ ? "YES" : "NO", child))) {
                  LOG_WARN("fail to build describe child stmt", K(ret));
                } else if (NULL == first) {
                  first = child;
                } else if (OB_FAIL(first->add_set_query(child))) {
                  LOG_WARN("fail to add set query", K(ret));
                }
              }
              if (OB_SUCC(ret)) {
                // widen VARCHAR aliases a bit: keep first child as the set root
                first->assign_set_op(ObSelectStmt::UNION);
                first->assign_set_all();
                OX (stmt_ = first);
                return ret;
              }
            }
          }
        }
      } else {
        explain_stmt->set_explain_query_stmt(static_cast<ObDMLStmt *>(child_stmt));
        ParseNode *format = parse_tree.children_[EXPLAIN_FORMAT];
        if (NULL != format) {
          switch (format->type_) {
          case T_OUTLINE:
          {
            explain_stmt->set_explain_format(EXPLAIN_OUTLINE);
            opt.with_tree_line_ = true;
          } break;
          case T_EXTENDED:
          {
            explain_stmt->set_explain_format(EXPLAIN_EXTENDED);
            opt.with_tree_line_ = true;
          } break;
          case T_PARTITIONS:
          {
            explain_stmt->set_explain_format(EXPLAIN_PARTITIONS);
            opt.with_tree_line_ = true;
          } break;
          case T_TRADITIONAL:
          {
            explain_stmt->set_explain_format(EXPLAIN_TRADITIONAL);
            opt.with_tree_line_ = true;
          } break;
          case T_FORMAT_JSON:
          {
            explain_stmt->set_explain_format(EXPLAIN_FORMAT_JSON);
            opt.with_tree_line_ = false;
          } break;
          case T_BASIC: {
            explain_stmt->set_explain_format(EXPLAIN_BASIC);
            opt.with_tree_line_ = true;
            break;
          }
          case T_EXTENDED_NOADDR: {
            explain_stmt->set_explain_format(EXPLAIN_EXTENDED_NOADDR);
            opt.with_tree_line_ = true;
            break;
          }
          case T_PLANREGRESS: {
            explain_stmt->set_explain_format(EXPLAIN_PLANREGRESS);
            opt.with_tree_line_ = true;
            break;
          }
          default:
            ret = OB_ERROR;
            LOG_WARN("unknown explain format", "format", format->type_);
            break;
          }
        }
      }
    }
    ParseNode *opt_node = parse_tree.children_[EXPLAIN_DISPLAY_OPTION];
    if (OB_SUCC(ret) && NULL != opt_node) {
      switch (opt_node->type_) {
      case T_PRETTY_COLOR:
        opt.with_color_ = true;
        // pass
      case T_PRETTY:
        opt.with_tree_line_ = true;
        break;
      default:
        ret = OB_ERR_UNEXPECTED;
      }
    }
    if (OB_SUCC(ret)) {
      explain_stmt->set_display_opt(opt);
    }
    ParseNode *into_table_node = parse_tree.children_[EXPLAIN_INTO_TABLE];
    if (OB_SUCC(ret) && NULL != into_table_node) {
      ObString into_table(into_table_node->str_len_, into_table_node->str_value_);
      explain_stmt->set_into_table(into_table);
    }
    ParseNode *statement_id_node = parse_tree.children_[EXPLAIN_STMT_ID];
    if (OB_SUCC(ret) && NULL != statement_id_node) {
      ObString statement_id(statement_id_node->str_len_, statement_id_node->str_value_);
      explain_stmt->set_statement_id(statement_id);
    }
  }

  return ret;
}
