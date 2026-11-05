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
#include "sql/resolver/ob_resolver.h"
#include "lib/utility/ob_smart_call.h"
#include "lib/stat/ob_diagnostic_info_guard.h"
#include "sql/resolver/cmd/ob_alter_system_resolver.h"
#include "sql/resolver/dml/ob_insert_resolver.h"
#include "sql/resolver/dml/ob_update_resolver.h"
#include "sql/resolver/dml/ob_delete_resolver.h"
#include "sql/resolver/tcl/ob_start_trans_resolver.h"
#include "sql/resolver/tcl/ob_end_trans_resolver.h"
#include "sql/resolver/prepare/ob_prepare_resolver.h"
#include "sql/resolver/prepare/ob_execute_resolver.h"
#include "sql/resolver/prepare/ob_deallocate_resolver.h"
#include "sql/resolver/ddl/ob_create_table_resolver.h"
#include "sql/resolver/ddl/ob_rename_table_resolver.h"
#include "sql/resolver/ddl/ob_truncate_table_resolver.h"
#include "sql/resolver/ddl/ob_fork_table_resolver.h"
#include "sql/resolver/ddl/ob_fork_database_resolver.h"
#include "sql/resolver/ddl/ob_create_table_like_resolver.h"
#include "sql/resolver/ddl/ob_alter_table_resolver.h"
#include "sql/resolver/ddl/ob_drop_table_resolver.h"
#include "sql/resolver/ddl/ob_create_index_resolver.h"
#include "sql/resolver/ddl/ob_drop_index_resolver.h"
#include "sql/resolver/ddl/ob_create_database_resolver.h"
#include "sql/resolver/ddl/ob_alter_database_resolver.h"
#include "sql/resolver/ddl/ob_use_database_resolver.h"
#include "sql/resolver/ddl/ob_drop_database_resolver.h"
#include "sql/resolver/ddl/ob_create_view_resolver.h"
#include "sql/resolver/ddl/ob_explain_resolver.h"
#include "sql/resolver/ddl/ob_create_outline_resolver.h"
#include "sql/resolver/ddl/ob_alter_outline_resolver.h"
#include "sql/resolver/ddl/ob_drop_outline_resolver.h"
#include "sql/resolver/ddl/ob_drop_routine_resolver.h"
#include "sql/resolver/ddl/ob_trigger_resolver.h"
#include "sql/resolver/ddl/ob_optimize_resolver.h"
#include "ddl/ob_drop_routine_resolver.h"
#include "ddl/ob_alter_routine_resolver.h"
#include "sql/resolver/ddl/ob_create_package_resolver.h"
#include "sql/resolver/ddl/ob_drop_package_resolver.h"
#include "sql/resolver/ddl/ob_recyclebin_restore_resolver.h"
#include "sql/resolver/ddl/ob_purge_resolver.h"
#include "sql/resolver/ddl/ob_analyze_stmt_resolver.h"
#include "sql/resolver/ddl/ob_purge_resolver.h"
#include "sql/resolver/ddl/ob_set_comment_resolver.h"
#include "sql/resolver/ddl/ob_lock_table_resolver.h"
#include "sql/resolver/dml/ob_insert_resolver.h"
#include "sql/resolver/dml/ob_update_resolver.h"
#include "sql/resolver/dml/ob_delete_resolver.h"
#include "sql/resolver/dcl/ob_create_user_resolver.h"
#include "sql/resolver/dcl/ob_drop_user_resolver.h"
#include "sql/resolver/dcl/ob_rename_user_resolver.h"
#include "sql/resolver/dcl/ob_set_password_resolver.h"
#include "sql/resolver/dcl/ob_lock_user_resolver.h"
#include "sql/resolver/dcl/ob_grant_resolver.h"
#include "sql/resolver/dcl/ob_revoke_resolver.h"
#include "sql/resolver/dcl/ob_create_role_resolver.h"
#include "sql/resolver/dcl/ob_drop_role_resolver.h"
#include "sql/resolver/dcl/ob_alter_user_role_resolver.h"
#include "sql/resolver/tcl/ob_start_trans_resolver.h"
#include "sql/resolver/tcl/ob_end_trans_resolver.h"
#include "tcl/ob_savepoint_resolver.h"
#include "sql/resolver/cmd/ob_variable_set_resolver.h"
#include "sql/resolver/cmd/ob_show_resolver.h"
#include "sql/resolver/cmd/ob_diff_table_resolver.h"
#include "sql/resolver/cmd/ob_merge_table_resolver.h"
#include "sql/resolver/cmd/ob_kill_resolver.h"
#include "sql/resolver/cmd/ob_set_names_resolver.h"
#include "sql/resolver/cmd/ob_set_transaction_resolver.h"
#include "sql/resolver/cmd/ob_empty_query_resolver.h"
#include "sql/resolver/cmd/ob_anonymous_block_resolver.h"
#include "sql/resolver/cmd/ob_call_procedure_resolver.h"
#include "sql/resolver/cmd/ob_load_data_resolver.h"
#include "sql/resolver/prepare/ob_execute_resolver.h"
#include "sql/resolver/prepare/ob_deallocate_resolver.h"
#include "sql/resolver/ddl/ob_purge_resolver.h"
#include "sql/resolver/ddl/ob_set_comment_resolver.h"
#include "sql/resolver/expr/ob_raw_expr_wrap_enum_set.h"
#include "sql/resolver/cmd/ob_get_diagnostics_resolver.h"
#include "sql/resolver/dcl/ob_alter_role_resolver.h"
#include "sql/resolver/dml/ob_select_resolver.h"
#include "sql/resolver/dml/ob_select_stmt.h"
#include "share/system_variable/ob_sys_var_class_type.h"
#include "share/file_sql/ob_file_scan_utils.h"
#include "share/schema/ob_schema_getter_guard.h"
#include "share/schema/ob_table_schema.h"
#include "sql/parser/ob_parser.h"
#include "sql/parser/parse_node.h"
#include <ctype.h>
#include "sql/pl/ob_pl_package.h"

namespace oceanbase
{
using namespace common;
using namespace share;
namespace sql
{
ObResolver::ObResolver(ObResolverParams &params)
    : params_(params)
{

}

ObResolver::~ObResolver()
{
}

template <typename ResolverType>
int ObResolver::stmt_resolver_func(ObResolverParams &params, const ParseNode &parse_tree, ObStmt *&stmt)
{
  int ret = OB_SUCCESS;
  HEAP_VAR(ResolverType, stmt_resolver, params) {
    if (OB_FAIL(SMART_CALL(stmt_resolver.resolve(parse_tree)))) {
    }
    stmt = stmt_resolver.get_basic_stmt();
  }
  return ret;
}

template <typename SelectResolverType>
int ObResolver::select_stmt_resolver_func(ObResolverParams &params, const ParseNode &parse_tree, ObStmt *&stmt)
{
  int ret = OB_SUCCESS;
  HEAP_VAR(SelectResolverType, stmt_resolver, params) {
    stmt_resolver.set_calc_found_rows(true);
    stmt_resolver.set_has_top_limit(true);
    if (OB_FAIL(SMART_CALL(stmt_resolver.resolve(parse_tree)))) {
    }
    stmt = stmt_resolver.get_basic_stmt();
  }
  return ret;
}

// COPY (select ...) TO 'file' (format csv, header, nullstr 'NULL'):
// parse the option list and fill the select-into item
static int resolve_copy_file_options(const ParseNode *opt_node, ObResolverParams &params,
                                     ObSelectIntoItem &into_item)
{
  int ret = OB_SUCCESS;
  const ObCollationType sys_cs = ObCharset::get_system_collation();
  for (const ParseNode *link = opt_node; OB_SUCC(ret) && NULL != link; link = link->children_[1]) {
    const ParseNode *opt = link->children_[0];
    if (OB_ISNULL(opt) || opt->num_child_ != 2 || OB_ISNULL(opt->children_[0])) {
      ret = OB_ERR_UNEXPECTED;
      LOG_WARN("invalid copy option node", K(ret));
    } else {
      ObString opt_name(opt->children_[0]->str_len_, opt->children_[0]->str_value_);
      const ParseNode *val_node = opt->children_[1];
      if (0 == opt_name.case_compare("format")) {
        if (OB_ISNULL(val_node)) {
          ret = OB_ERR_PARSE_SYNTAX;
        } else {
          ObString fmt(val_node->str_len_, val_node->str_value_);
          if (0 != fmt.case_compare("csv")) {
            ret = OB_NOT_SUPPORTED;
            LOG_USER_ERROR(OB_NOT_SUPPORTED, "this format type");
          }
        }
      } else if (0 == opt_name.case_compare("header")) {
        into_item.write_header_ = (NULL == val_node) ? true : (0 != val_node->value_);
      } else if (0 == opt_name.case_compare("nullstr")) {
        if (OB_ISNULL(val_node)) {
          ret = OB_ERR_PARSE_SYNTAX;
        } else {
          ObString nv(val_node->str_len_, val_node->str_value_);
          if (OB_FAIL(ob_write_string(*params.allocator_, nv, into_item.null_str_, true))) {
            LOG_WARN("fail to copy nullstr", K(ret));
          }
        }
      } else if (0 == opt_name.case_compare("delimiter")) {
        if (OB_ISNULL(val_node)) {
          ret = OB_ERR_PARSE_SYNTAX;
        } else {
          into_item.field_str_.set_varchar(ObString(val_node->str_len_, val_node->str_value_));
          into_item.field_str_.set_collation_type(sys_cs);
        }
      } else {
        ret = OB_NOT_SUPPORTED;
        LOG_USER_ERROR(OB_NOT_SUPPORTED, "this copy option");
      }
    }
  }
  return ret;
}

// COPY (select ...) TO 'file' (...) -> rewrite as SELECT ... INTO OUTFILE
static int resolve_copy_file_into(ObResolverParams &params, const ParseNode &parse_tree, ObStmt &stmt)
{
  int ret = OB_SUCCESS;
  int64_t enable_file_sql = 0;
  ObSQLSessionInfo *session = params.session_info_;
  ObSelectStmt *select_stmt = static_cast<ObSelectStmt *>(&stmt);
  ObSelectIntoItem *into_item = NULL;
  const ParseNode *path_node = parse_tree.children_[1];
  const ParseNode *opt_node = parse_tree.children_[2];
  ObString path(path_node->str_len_, path_node->str_value_);
  if (OB_ISNULL(session) || OB_ISNULL(params.allocator_)) {
    ret = OB_ERR_UNEXPECTED;
  } else if (OB_FAIL(session->get_sys_variable(share::SYS_VAR_ENABLE_FILE_SQL, enable_file_sql))) {
    LOG_WARN("fail to get enable_file_sql", K(ret));
  } else if (enable_file_sql <= 0) {
    ret = OB_NOT_SUPPORTED;
    LOG_USER_ERROR(OB_NOT_SUPPORTED, "file sql is disabled, set enable_file_sql to enable it");
  }
  // secure_file_priv check
  if (OB_FAIL(ret)) {
    // nothing
  } else {
    ObString secure_priv;
    bool allowed = false;
    if (OB_FAIL(session->get_sys_variable(share::SYS_VAR_SECURE_FILE_PRIV, secure_priv))) {
      LOG_WARN("fail to get secure_file_priv", K(ret));
    } else if (OB_FAIL(file_sql::check_file_path_allowed(secure_priv, path, allowed))) {
      LOG_WARN("fail to check file path", K(ret));
    } else if (!allowed) {
      ret = OB_NOT_SUPPORTED;
      LOG_USER_ERROR(OB_NOT_SUPPORTED, "path outside of secure_file_priv");
    }
  }
  if (OB_FAIL(ret)) {
    // nothing
  } else if (OB_ISNULL(into_item = static_cast<ObSelectIntoItem *>(
               params.allocator_->alloc(sizeof(ObSelectIntoItem))))) {
    ret = OB_ALLOCATE_MEMORY_FAILED;
  } else {
    new (into_item) ObSelectIntoItem();
    into_item->into_type_ = T_INTO_OUTFILE;
    into_item->outfile_name_.set_varchar(path);
    into_item->outfile_name_.set_collation_type(ObCharset::get_system_collation());
    // DuckDB COPY TO defaults: comma separated, newline terminated
    into_item->field_str_.set_varchar(",");
    into_item->field_str_.set_collation_type(ObCharset::get_system_collation());
    into_item->line_str_.set_varchar("\n");
    into_item->line_str_.set_collation_type(ObCharset::get_system_collation());
    // column names for the header line
    for (int64_t i = 0; OB_SUCC(ret) && i < select_stmt->get_select_item_size(); ++i) {
      const SelectItem &item = select_stmt->get_select_item(i);
      ObString name = item.alias_name_.empty() ? item.expr_name_ : item.alias_name_;
      if (OB_FAIL(into_item->column_names_.push_back(name))) {
        LOG_WARN("fail to push column name", K(ret));
      }
    }
    if (OB_FAIL(ret)) {
      // nothing
    } else if (OB_FAIL(resolve_copy_file_options(opt_node, params, *into_item))) {
      LOG_WARN("fail to resolve copy options", K(ret));
    }
    if (OB_SUCC(ret)) {
      select_stmt->set_select_into(into_item);
    }
  }
  return ret;
}


// ---------------------------------------------------------------------------
// AI COLUMN statements: SHOW AI COLUMNS / ALTER TABLE ... RETRY AI COLUMN
// Both are implemented by generating an equivalent SELECT/UPDATE text and
// resolving it, which keeps the executor untouched.
// ---------------------------------------------------------------------------

static const ParseNode *find_ai_column_retry_node(const ParseNode &node)
{
  const ParseNode *found = NULL;
  if (T_AI_COLUMN_RETRY == node.type_) {
    found = &node;
  } else if (NULL != node.children_) {
    for (int32_t i = 0; NULL == found && i < node.num_child_; ++i) {
      if (NULL != node.children_[i]) {
        found = find_ai_column_retry_node(*node.children_[i]);
      }
    }
  }
  return found;
}

static int ai_column_extract_relation_names(const ParseNode *node,
                                            ObString &db_name,
                                            ObString &table_name)
{
  int ret = OB_SUCCESS;
  if (OB_ISNULL(node) || T_RELATION_FACTOR != node->type_ || node->num_child_ < 2
      || OB_ISNULL(node->children_)) {
    ret = OB_ERR_UNEXPECTED;
  } else {
    const ParseNode *db_node = node->children_[0];
    const ParseNode *table_node = node->children_[1];
    if (OB_NOT_NULL(db_node)) {
      db_name.assign_ptr(db_node->str_value_, static_cast<ObString::obstr_size_t>(db_node->str_len_));
    }
    if (OB_NOT_NULL(table_node)) {
      table_name.assign_ptr(table_node->str_value_,
                            static_cast<ObString::obstr_size_t>(table_node->str_len_));
    }
  }
  return ret;
}

static int ai_column_append_ident(ObSqlString &sql, const ObString &name)
{
  int ret = OB_SUCCESS;
  if (OB_FAIL(sql.append("`"))) {
  } else if (OB_FAIL(sql.append(name))) {
  } else if (OB_FAIL(sql.append("`"))) {
  }
  return ret;
}

static int ai_column_append_literal(ObSqlString &sql, const ObString &value)
{
  int ret = OB_SUCCESS;
  if (OB_FAIL(sql.append("'"))) {
  }
  for (int64_t i = 0; OB_SUCC(ret) && i < value.length(); ++i) {
    const char c = value.ptr()[i];
    if ('\'' == c) {
      ret = sql.append("''");
    } else {
      ret = sql.append(&c, 1);
    }
  }
  if (OB_SUCC(ret)) {
    ret = sql.append("'");
  }
  return ret;
}

static void ai_column_get_func_name(const ObString &expr_text, ObString &func_name)
{
  int64_t pos = 0;
  while (pos < expr_text.length() && ' ' == expr_text[pos]) {
    ++pos;
  }
  const int64_t begin = pos;
  while (pos < expr_text.length()
         && (isalnum(static_cast<unsigned char>(expr_text[pos])) || '_' == expr_text[pos])) {
    ++pos;
  }
  func_name.assign_ptr(expr_text.ptr() + begin, static_cast<ObString::obstr_size_t>(pos - begin));
}

int ObResolver::resolve_generated_sql(const common::ObString &sql, ObStmt *&stmt)
{
  int ret = OB_SUCCESS;
  ObParser parser(*params_.allocator_,
                  params_.session_info_->get_sql_mode(),
                  params_.session_info_->get_charsets4parser());
  ParseResult parse_result;
  ParseNode *node = NULL;
  if (OB_FAIL(parser.parse(sql, parse_result))) {
    LOG_WARN("fail to parse generated sql", K(ret), K(sql));
  } else if (OB_ISNULL(parse_result.result_tree_) || parse_result.result_tree_->num_child_ < 1
             || OB_ISNULL(node = parse_result.result_tree_->children_[0])) {
    ret = OB_ERR_UNEXPECTED;
    LOG_WARN("invalid generated parse tree", K(ret), K(sql));
  } else if (T_SELECT == node->type_) {
    if (OB_FAIL(select_stmt_resolver_func<ObSelectResolver>(params_, *node, stmt))) {
    } else {
      params_.session_info_->set_stmt_type(stmt::T_SELECT);
    }
  } else if (T_UPDATE == node->type_) {
    if (OB_FAIL(stmt_resolver_func<ObUpdateResolver>(params_, *node, stmt))) {
    } else {
      params_.session_info_->set_stmt_type(stmt::T_UPDATE);
    }
  } else {
    ret = OB_ERR_UNEXPECTED;
    LOG_WARN("unexpected generated statement", K(ret), K(node->type_));
  }
  return ret;
}

int ObResolver::resolve_show_ai_columns(const ParseNode &parse_tree, ObStmt *&stmt)
{
  int ret = OB_SUCCESS;
  ObSqlString sql;
  ObSchemaGetterGuard *guard = NULL;
  ObSEArray<const ObTableSchema *, 16> tables;
  ParseNode *target_node = NULL;
  if (OB_ISNULL(params_.schema_checker_) || OB_ISNULL(params_.session_info_)) {
    ret = OB_NOT_INIT;
  } else if (OB_ISNULL(guard = params_.schema_checker_->get_schema_guard())) {
    ret = OB_ERR_UNEXPECTED;
  } else if (parse_tree.num_child_ > 0) {
    target_node = parse_tree.children_[0];
  }
  if (OB_FAIL(ret)) {
  } else if (NULL != target_node) {
    ObString db_name;
    ObString table_name;
    const ObTableSchema *table = NULL;
    if (OB_ISNULL(target_node->children_) || target_node->num_child_ < 2) {
      ret = OB_ERR_UNEXPECTED;
    } else if (OB_FAIL(ai_column_extract_relation_names(target_node->children_[1],
                                                        db_name,
                                                        table_name))) {
      LOG_WARN("fail to resolve relation name", K(ret));
    } else if (db_name.empty() && OB_FAIL(params_.session_info_->get_database_name(db_name))) {
      LOG_WARN("fail to get current database", K(ret));
    } else if (OB_FAIL(guard->get_table_schema(db_name, table_name, false, table))) {
      LOG_WARN("fail to get table schema", K(ret), K(db_name), K(table_name));
    } else if (OB_ISNULL(table)) {
      ret = OB_ERR_UNEXPECTED;
    } else if (OB_FAIL(tables.push_back(table))) {
      LOG_WARN("fail to push table schema", K(ret));
    }
  } else {
    ObString db_name;
    const ObDatabaseSchema *db_schema = NULL;
    if (OB_FAIL(params_.session_info_->get_database_name(db_name))) {
      LOG_WARN("fail to get current database", K(ret));
    } else if (OB_FAIL(guard->get_database_schema(db_name, db_schema))) {
      LOG_WARN("fail to get database schema", K(ret), K(db_name));
    } else if (OB_ISNULL(db_schema)) {
      ret = OB_ERR_UNEXPECTED;
    } else if (OB_FAIL(guard->get_table_schemas_in_database(db_schema->get_database_id(), tables))) {
      LOG_WARN("fail to get table schemas", K(ret));
    }
  }
  if (OB_FAIL(ret)) {
  } else {
    bool first = true;
    for (int64_t i = 0; OB_SUCC(ret) && i < tables.count(); ++i) {
      const ObTableSchema *table = tables.at(i);
      if (OB_ISNULL(table)) {
        continue;
      }
      const ObString table_name = table->get_table_name_str();
      for (ObTableSchema::const_column_iterator iter = table->column_begin();
           OB_SUCC(ret) && iter != table->column_end();
           ++iter) {
        const ObColumnSchemaV2 *column = *iter;
        ObString func_name;
        if (OB_ISNULL(column) || !column->is_ai_column()) {
          // do nothing
        } else if (!first && OB_FAIL(sql.append(" UNION ALL "))) {
        } else if (OB_FAIL(sql.append("SELECT "))) {
        } else if (OB_FAIL(ai_column_append_literal(sql, table_name))) {
        } else if (OB_FAIL(sql.append(" AS "))) {
        } else if (OB_FAIL(ai_column_append_ident(sql, "Table"))) {
        } else if (OB_FAIL(sql.append(", "))) {
        } else if (OB_FAIL(ai_column_append_literal(sql, column->get_column_name_str()))) {
        } else if (OB_FAIL(sql.append(" AS "))) {
        } else if (OB_FAIL(ai_column_append_ident(sql, "Column"))) {
        } else if (OB_FALSE_IT(ai_column_get_func_name(column->get_ai_meta().expr_text_, func_name))) {
        } else if (OB_FAIL(sql.append(", "))) {
        } else if (OB_FAIL(ai_column_append_literal(sql, func_name))) {
        } else if (OB_FAIL(sql.append(" AS "))) {
        } else if (OB_FAIL(ai_column_append_ident(sql, "Function"))) {
        } else if (OB_FAIL(sql.append(", "))) {
        } else if (OB_FAIL(ai_column_append_literal(sql, column->get_ai_meta().model_name_))) {
        } else if (OB_FAIL(sql.append(" AS "))) {
        } else if (OB_FAIL(ai_column_append_ident(sql, "Model"))) {
        } else if (OB_FAIL(sql.append(", "))) {
        } else if (OB_FAIL(ai_column_append_literal(sql, column->get_ai_meta().sync_mode_))) {
        } else if (OB_FAIL(sql.append(" AS "))) {
        } else if (OB_FAIL(ai_column_append_ident(sql, "Sync_Mode"))) {
        } else if (OB_FAIL(sql.append(", "))) {
        } else if (OB_FAIL(sql.append_fmt("%ld", column->get_ai_meta().max_retries_))) {
        } else if (OB_FAIL(sql.append(" AS "))) {
        } else if (OB_FAIL(ai_column_append_ident(sql, "Max_Retries"))) {
        } else {
          first = false;
        }
      }
    }
    if (OB_SUCC(ret) && first) {
      // no AI column, emit a header-only empty result
      if (OB_FAIL(sql.append("SELECT '' AS `Table`, '' AS `Column`, '' AS `Function`, "
                             "'' AS `Model`, '' AS `Sync_Mode`, 0 AS `Max_Retries` WHERE 1 = 0"))) {
      }
    }
  }
  if (OB_SUCC(ret)) {
    LOG_INFO("resolve show ai columns", K(sql));
    if (OB_FAIL(resolve_generated_sql(sql.string(), stmt))) {
      LOG_WARN("fail to resolve generated show ai columns", K(ret), K(sql));
    }
  }
  return ret;
}

// The RETRY predicate is authored against the virtual STATUS pseudo column whose
// domain is READY / FAILED / PENDING, while the physical status column stores the
// short codes. The predicate text is therefore rewritten token by token: an
// unquoted `status` identifier addresses the status column, and its literals map
// to the stored codes. Back quoted identifiers are copied verbatim, so `status`
// still points at the user column of the same name.
static int ai_column_retry_emit_literal(ObSqlString &out,
                                        const ObString &input,
                                        const int64_t begin,
                                        const int64_t end)
{
  int ret = OB_SUCCESS;
  ObString text;
  text.assign_ptr(input.ptr() + begin, static_cast<ObString::obstr_size_t>(end - begin));
  if (0 == text.case_compare("READY")) {
    ret = out.append("'R'");
  } else if (0 == text.case_compare("FAILED")) {
    ret = out.append("'F'");
  } else if (0 == text.case_compare("PENDING")) {
    ret = out.append("'P'");
  } else if (OB_FAIL(out.append("'"))) {
  } else {
    for (int64_t i = 0; OB_SUCC(ret) && i < text.length(); ++i) {
      const char c = text.ptr()[i];
      if ('\'' == c) {
        ret = out.append("''");
      } else {
        ret = out.append(&c, 1);
      }
    }
    if (OB_SUCC(ret)) {
      ret = out.append("'");
    }
  }
  return ret;
}

static int ai_column_build_retry_predicate(const ObString &input,
                                           const ObString &status_name,
                                           ObSqlString &out)
{
  int ret = OB_SUCCESS;
  int64_t i = 0;
  while (OB_SUCC(ret) && i < input.length()) {
    const char c = input.ptr()[i];
    if ('\'' == c) {
      const int64_t begin = i + 1;
      int64_t end = begin;
      while (end < input.length()) {
        if ('\'' == input.ptr()[end]) {
          if (end + 1 < input.length() && '\'' == input.ptr()[end + 1]) {
            end += 2; // escaped quote inside the literal
            continue;
          }
          break;
        }
        ++end;
      }
      if (OB_FAIL(ai_column_retry_emit_literal(out, input, begin, end))) {
      } else {
        i = end + 1;
      }
    } else if ('`' == c) {
      int64_t end = i + 1;
      while (end < input.length() && '`' != input.ptr()[end]) {
        ++end;
      }
      if (end >= input.length()) {
        ret = OB_ERR_UNEXPECTED;
        LOG_WARN("unterminated quoted identifier in retry predicate", K(ret), K(input));
      } else if (OB_FAIL(out.append(input.ptr() + i, end + 1 - i))) {
      } else {
        i = end + 1;
      }
    } else if (isalnum(static_cast<unsigned char>(c)) || '_' == c || '$' == c) {
      int64_t end = i;
      while (end < input.length()
             && (isalnum(static_cast<unsigned char>(input.ptr()[end]))
                 || '_' == input.ptr()[end] || '$' == input.ptr()[end])) {
        ++end;
      }
      ObString ident;
      ident.assign_ptr(input.ptr() + i, static_cast<ObString::obstr_size_t>(end - i));
      if (0 == ident.case_compare("status")) {
        ret = ai_column_append_ident(out, status_name);
      } else {
        ret = out.append(ident.ptr(), ident.length());
      }
      i = end;
    } else {
      ret = out.append(&c, 1);
      ++i;
    }
  }
  return ret;
}

int ObResolver::resolve_ai_column_retry(const ParseNode &parse_tree, ObStmt *&stmt)
{
  int ret = OB_SUCCESS;
  ObSqlString sql;
  ObSqlString predicate;
  const ParseNode *action_list = NULL;
  const ParseNode *retry_node = NULL;
  const ParseNode *column_ref = NULL;
  const ParseNode *where_node = NULL;
  ObString db_name;
  ObString table_name;
  ObString column_name;
  if (T_ALTER_TABLE != parse_tree.type_ || parse_tree.num_child_ < 2
      || OB_ISNULL(parse_tree.children_)) {
    ret = OB_ERR_UNEXPECTED;
    LOG_WARN("invalid alter table parse tree", K(ret), K(parse_tree.num_child_));
  } else if (OB_ISNULL(action_list = parse_tree.children_[1])) {
    ret = OB_ERR_UNEXPECTED;
    LOG_WARN("alter table action list is null", K(ret));
  } else if (OB_ISNULL(retry_node = find_ai_column_retry_node(*action_list))) {
    ret = OB_ERR_UNEXPECTED;
    LOG_WARN("retry ai column node not found", K(ret));
  } else if (1 != action_list->num_child_) {
    // RETRY targets exactly one AI column and cannot be mixed with any other action
    ret = OB_NOT_SUPPORTED;
    LOG_USER_ERROR(OB_NOT_SUPPORTED, "combining AI COLUMN RETRY with another ALTER action");
  } else if (2 != retry_node->num_child_ || OB_ISNULL(retry_node->children_)
             || OB_ISNULL(column_ref = retry_node->children_[0])
             || T_COLUMN_REF != column_ref->type_ || 3 != column_ref->num_child_
             || OB_ISNULL(column_ref->children_) || OB_ISNULL(column_ref->children_[2])) {
    ret = OB_ERR_UNEXPECTED;
    LOG_WARN("invalid retry ai column parse tree", K(ret));
  } else if (OB_FAIL(ai_column_extract_relation_names(parse_tree.children_[0],
                                                      db_name,
                                                      table_name))) {
    LOG_WARN("fail to get relation name", K(ret));
  } else if (db_name.empty() && OB_FAIL(params_.session_info_->get_database_name(db_name))) {
    LOG_WARN("fail to get current database", K(ret));
  } else {
    const ParseNode *column_name_node = column_ref->children_[2];
    // the WHERE clause is optional: without it every row is recomputed
    where_node = retry_node->children_[1];
    column_name.assign_ptr(column_name_node->str_value_,
                           static_cast<ObString::obstr_size_t>(column_name_node->str_len_));
  }

  if (OB_SUCC(ret)) {
    const ObTableSchema *table = NULL;
    const ObColumnSchemaV2 *ai_column = NULL;
    const ObColumnSchemaV2 *status_column = NULL;
    ObSchemaGetterGuard *guard = params_.schema_checker_->get_schema_guard();
    if (OB_ISNULL(guard)) {
      ret = OB_ERR_UNEXPECTED;
      LOG_WARN("schema guard is null", K(ret));
    } else if (OB_FAIL(guard->get_table_schema(db_name, table_name, false, table))) {
      LOG_WARN("fail to get table schema", K(ret), K(db_name), K(table_name));
    } else if (OB_ISNULL(table)) {
      ret = OB_ERR_UNEXPECTED;
      LOG_WARN("table schema is null", K(ret), K(db_name), K(table_name));
    } else if (OB_ISNULL(ai_column = table->get_column_schema(column_name))) {
      ret = OB_ERR_BAD_FIELD_ERROR;
      LOG_USER_ERROR(OB_ERR_BAD_FIELD_ERROR,
                     column_name.length(), column_name.ptr(),
                     table->get_table_name_str().length(), table->get_table_name_str().ptr());
    } else if (!ai_column->is_ai_column()) {
      ret = OB_NOT_SUPPORTED;
      LOG_USER_ERROR(OB_NOT_SUPPORTED, "retrying a column that is not an AI column");
    } else if (OB_ISNULL(status_column = table->get_ai_status_column(*ai_column))) {
      ret = OB_ERR_UNEXPECTED;
      LOG_WARN("ai column has no status column", K(ret), KPC(ai_column));
    } else if (NULL != where_node
               && OB_FAIL(ai_column_build_retry_predicate(
                    ObString(where_node->str_len_, where_node->str_value_),
                    status_column->get_column_name_str(),
                    predicate))) {
      LOG_WARN("fail to build retry predicate", K(ret));
    } else if (OB_FAIL(sql.append("UPDATE "))) {
    } else if (OB_FAIL(ai_column_append_ident(sql, db_name))) {
    } else if (OB_FAIL(sql.append("."))) {
    } else if (OB_FAIL(ai_column_append_ident(sql, table->get_table_name_str()))) {
    } else if (OB_FAIL(sql.append(" SET "))) {
    } else if (OB_FAIL(ai_column_append_ident(sql, status_column->get_column_name_str()))) {
    } else if (OB_FAIL(sql.append(" = 'P', "))) {
    } else if (OB_FAIL(ai_column_append_ident(sql, ai_column->get_column_name_str()))) {
    } else if (OB_FAIL(sql.append(" = NULL"))) {
    } else if (0 != predicate.length()) {
      ret = sql.append(" WHERE ");
      if (OB_SUCC(ret)) {
        ret = sql.append(predicate);
      }
    }
    if (OB_FAIL(ret)) {
    } else {
      // the rewrite assigns an AI column, which is normally forbidden
      int64_t old_worker_flag = 0;
      ObObj new_flag;
      new_flag.set_int(1);
      if (OB_FAIL(params_.session_info_->get_sys_variable(SYS_VAR_AI_COLUMN_WORKER, old_worker_flag))) {
        LOG_WARN("fail to get ai column worker flag", K(ret));
      } else if (OB_FAIL(params_.session_info_->update_sys_variable(SYS_VAR_AI_COLUMN_WORKER, new_flag))) {
        LOG_WARN("fail to set ai column worker flag", K(ret));
      } else if (OB_FAIL(resolve_generated_sql(sql.string(), stmt))) {
        LOG_WARN("fail to resolve retry ai column", K(ret), K(sql));
      }
      ObObj restore_flag;
      restore_flag.set_int(old_worker_flag);
      int tmp_ret = params_.session_info_->update_sys_variable(SYS_VAR_AI_COLUMN_WORKER, restore_flag);
      if (OB_SUCCESS != tmp_ret) {
        LOG_WARN("fail to restore ai column worker flag", K(tmp_ret));
      }
    }
  }
  return ret;
}

int ObResolver::resolve(IsPrepared if_prepared, const ParseNode &parse_tree, ObStmt *&stmt)
{
#define REGISTER_STMT_RESOLVER(name)                                   \
  do {                                                                 \
    ret = stmt_resolver_func<Ob##name##Resolver>(params_, *real_parse_tree, stmt); \
  } while (0)

#define REGISTER_SELECT_STMT_RESOLVER(name)                                   \
  do {                                                                        \
    ret = select_stmt_resolver_func<Ob##name##Resolver>(params_, parse_tree, stmt); \
  } while (0)
  ACTIVE_SESSION_FLAG_SETTER_GUARD(in_resolve);
  int ret = OB_SUCCESS;
  const ParseNode *real_parse_tree = NULL;
  int64_t questionmark_count = 0;
  UNUSED(if_prepared);
  if (OB_ISNULL(params_.allocator_)
      || OB_ISNULL(params_.schema_checker_)
      || OB_ISNULL(params_.session_info_)
      || OB_ISNULL(params_.expr_factory_)
      || OB_ISNULL(params_.query_ctx_)) {
    ret = OB_NOT_INIT;
  } else {
    real_parse_tree = &parse_tree;
  }

  if (OB_SUCC(ret)) {
    params_.resolver_scope_stmt_type_ = parse_tree.type_;
    params_.session_info_->set_stmt_type(
      ObResolverUtils::get_stmt_type_by_item_type(parse_tree.type_));
    ObExecContext *exec_ctx = params_.session_info_->get_cur_exec_ctx();
    if (exec_ctx != nullptr && exec_ctx->get_sql_ctx() != nullptr) {
      //save the current stmt type on my context,
      //some other modules may need to get the current SQL stmt type through its context
      ObSqlCtx *sql_ctx = exec_ctx->get_sql_ctx();
      sql_ctx->stmt_type_ = ObResolverUtils::get_stmt_type_by_item_type(parse_tree.type_);
    }

    switch (real_parse_tree->type_) {
      case T_CREATE_TABLE: {
        REGISTER_STMT_RESOLVER(CreateTable);
        break;
      }
      case T_ALTER_TABLE: {
        // ALTER TABLE t RETRY AI COLUMN c [WHERE ...] is rewritten into an
        // UPDATE that puts the selected rows back into PENDING state.
        const ParseNode *retry_node = find_ai_column_retry_node(parse_tree);
        if (OB_NOT_NULL(retry_node)) {
          if (OB_FAIL(resolve_ai_column_retry(parse_tree, stmt))) {
            LOG_WARN("fail to resolve retry ai column", K(ret));
          }
        } else {
          REGISTER_STMT_RESOLVER(AlterTable);
        }
        break;
      }
      case T_SHOW_AI_COLUMNS: {
        if (OB_FAIL(resolve_show_ai_columns(parse_tree, stmt))) {
          LOG_WARN("fail to resolve show ai columns", K(ret));
        }
        break;
      }
      case T_CREATE_INDEX: {
        REGISTER_STMT_RESOLVER(CreateIndex);
        break;
      }
      case T_CREATE_VIEW: {
        REGISTER_STMT_RESOLVER(CreateView);
        break;
      }
      case T_ALTER_VIEW: {
#if 0
        ObAlterViewResolver stmt_resolver(params_);
        if (OB_FAIL(stmt_resolver.resolve(*node))) {
        }

        stmt = stmt_resolver.get_basic_stmt();
#endif
        ret = OB_NOT_SUPPORTED;
        LOG_WARN("not support to alter view");
	      LOG_USER_ERROR(OB_NOT_SUPPORTED, "alter view");
        break;
      }
      case T_DROP_VIEW:
      case T_DROP_TABLE: {
        REGISTER_STMT_RESOLVER(DropTable);
        break;
      }
      case T_DROP_INDEX: {
        REGISTER_STMT_RESOLVER(DropIndex);
        break;
      }
      case T_CREATE_TABLE_LIKE: {
        REGISTER_STMT_RESOLVER(CreateTableLike);
        break;
      }
      case T_FORK_TABLE: {
        REGISTER_STMT_RESOLVER(ForkTable);
        break;
      }
      case T_FORK_DATABASE: {
        REGISTER_STMT_RESOLVER(ForkDatabase);
        break;
      }
      case T_DIFF_TABLE: {
        REGISTER_STMT_RESOLVER(DiffTable);
        break;
      }
      case T_MERGE_TABLE: {
        REGISTER_STMT_RESOLVER(MergeTable);
        break;
      }
      case T_SELECT: {
        REGISTER_SELECT_STMT_RESOLVER(Select);
        break;
      }
      case T_COPY_FILE_STMT: {
        // rewrite COPY (select ...) TO 'file' (...) as SELECT ... INTO OUTFILE
        if (OB_FAIL(select_stmt_resolver_func<ObSelectResolver>(
                params_, *parse_tree.children_[0], stmt))) {
        } else if (OB_FAIL(resolve_copy_file_into(params_, parse_tree, *stmt))) {
        }
        break;
      }
      case T_INSERT: {
        REGISTER_STMT_RESOLVER(Insert);
        break;
      }
      case T_UPDATE: {
        REGISTER_STMT_RESOLVER(Update);
        break;
      }
      case T_DELETE: {
        REGISTER_STMT_RESOLVER(Delete);
        break;
      }
      case T_BEGIN: {
        REGISTER_STMT_RESOLVER(StartTrans);
        break;
      }
      case T_ROLLBACK:
        //fall through
      case T_COMMIT: {
        REGISTER_STMT_RESOLVER(EndTrans);
        break;
      }
      case T_FREEZE: {
        REGISTER_STMT_RESOLVER(Freeze);
        break;
      }
      case T_FLUSH_CACHE: {
        REGISTER_STMT_RESOLVER(FlushCache);
        break;
      }
      case T_FLUSH_KVCACHE: {
        REGISTER_STMT_RESOLVER(FlushKVCache);
        break;
      }
      case T_FLUSH_ILOGCACHE: {
        REGISTER_STMT_RESOLVER(FlushIlogCache);
        break;
      }
      case T_FLUSH_DAG_WARNINGS: {
        REGISTER_STMT_RESOLVER(FlushDagWarnings);
        break;
      }
      case T_MERGE_CONTROL: {
        REGISTER_STMT_RESOLVER(AdminMerge);
        break;
      }
      case T_CANCEL_TASK: {
        REGISTER_STMT_RESOLVER(CancelTask);
        break;
      }
      case T_REFRESH_MEMORY_STAT: {
        REGISTER_STMT_RESOLVER(RefreshMemStat);
        break;
      }
      case T_REFRESH_IO_CALIBRATION: {
        REGISTER_STMT_RESOLVER(RefreshIOCalibration);
        break;
      }
      case T_SWITCHOVER_TO_STANDBY:
      case T_SWITCHOVER_TO_PRIMARY:
      case T_ACTIVATE_STANDBY: {
        REGISTER_STMT_RESOLVER(SwitchRole);
        break;
      }
      case T_ALTER_SYSTEM_SET_PARAMETER: {
        REGISTER_STMT_RESOLVER(SetConfig);
        break;
      }
      case T_ALTER_SYSTEM_SETTP: {
        REGISTER_STMT_RESOLVER(SetTP);
        break;
      }
      case T_CLEAR_MERGE_ERROR: {
        REGISTER_STMT_RESOLVER(ClearMergeError);
        break;
      }
      case T_CREATE_DATABASE: {
        REGISTER_STMT_RESOLVER(CreateDatabase);
        break;
      }
      case T_USE_DATABASE: {
        REGISTER_STMT_RESOLVER(UseDatabase);
        break;
      }
      case T_ALTER_DATABASE: {
        REGISTER_STMT_RESOLVER(AlterDatabase);
        break;
      }
      case T_DROP_DATABASE: {
        REGISTER_STMT_RESOLVER(DropDatabase);
        break;
      }
      case T_RENAME_TABLE: {
        REGISTER_STMT_RESOLVER(RenameTable);
        break;
      }
      case T_TRUNCATE_TABLE: {
        REGISTER_STMT_RESOLVER(TruncateTable);
        break;
      }
      case T_RECYCLEBIN_RESTORE_TABLE: {
        REGISTER_STMT_RESOLVER(RecyclebinRestoreTable);
        break;
      }
      case T_RECYCLEBIN_RESTORE_DATABASE: {
        REGISTER_STMT_RESOLVER(RecyclebinRestoreDatabase);
        break;
      }
      case T_PURGE_TABLE: {
        REGISTER_STMT_RESOLVER(PurgeTable);
        break;
      }
      case T_PURGE_INDEX: {
        REGISTER_STMT_RESOLVER(PurgeIndex);
        break;
      }
      case T_PURGE_DATABASE: {
        REGISTER_STMT_RESOLVER(PurgeDatabase);
        break;
      }
      case T_PURGE_RECYCLEBIN: {
        REGISTER_STMT_RESOLVER(PurgeRecycleBin);
        break;
      }
      case T_OPTIMIZE_TABLE: {
        REGISTER_STMT_RESOLVER(OptimizeTable);
        break;
      }
      case T_PREPARE: {
        if (params_.is_prepare_protocol_) {
          ret = OB_ERR_PARSE_SQL;
        } else {
          REGISTER_STMT_RESOLVER(Prepare);
        }
        break;
      }
      case T_EXECUTE: {
        if (params_.is_prepare_protocol_) {
          ret = OB_ERR_PARSE_SQL;
        } else {
          REGISTER_STMT_RESOLVER(Execute);
        }
        break;
      }
      case T_DEALLOCATE: {
        if (params_.is_prepare_protocol_) {
          ret = OB_ERR_PARSE_SQL;
        } else {
          REGISTER_STMT_RESOLVER(Deallocate);
        }
        break;
      }
      case T_VARIABLE_SET: {
        REGISTER_STMT_RESOLVER(VariableSet);
        break;
      }
      case T_TRANSACTION: {
        REGISTER_STMT_RESOLVER(SetTransaction);
        break;
      }
      case T_SHOW_TABLES:
      case T_SHOW_DATABASES:
      case T_SHOW_VARIABLES:
      case T_SHOW_COLUMNS:
      case T_SHOW_SCHEMA:
      case T_SHOW_CREATE_DATABASE:
      case T_SHOW_CREATE_TABLE:
      case T_SHOW_CREATE_VIEW:
      case T_SHOW_CREATE_PROCEDURE:
      case T_SHOW_CREATE_FUNCTION:
      case T_SHOW_TABLE_STATUS:
      case T_SHOW_PARAMETERS:
      case T_SHOW_INDEXES:
      case T_SHOW_PROCESSLIST:
      case T_SHOW_WARNINGS:
      case T_SHOW_ERRORS:
      case T_SHOW_TRACE:
      case T_SHOW_ENGINES:
      case T_SHOW_PROFILE:
      case T_SHOW_ENGINE:
      case T_SHOW_OPEN_TABLES:
      case T_SHOW_PRIVILEGES:
      case T_SHOW_CHARSET:
      case T_SHOW_COLLATION:
      case T_SHOW_GRANTS:
      case T_SHOW_RECYCLEBIN:
      case T_SHOW_PROCEDURE_STATUS:
      case T_SHOW_FUNCTION_STATUS:
      case T_SHOW_TRIGGERS:
      case T_SHOW_STATUS:
      case T_SHOW_CREATE_TRIGGER:
      case T_SHOW_CHECK_TABLE:
      case T_SHOW_CREATE_USER: {
        REGISTER_STMT_RESOLVER(Show);
        break;
      }
      case T_CREATE_USER: {
        REGISTER_STMT_RESOLVER(CreateUser);
        break;
      }
      case T_DROP_USER: {
        REGISTER_STMT_RESOLVER(DropUser);
        break;
      }
      case T_RENAME_USER: {
        REGISTER_STMT_RESOLVER(RenameUser);
        break;
      }
      case T_SET_PASSWORD: {
        REGISTER_STMT_RESOLVER(SetPassword);
        break;
      }
      case T_LOCK_USER: {
        REGISTER_STMT_RESOLVER(LockUser);
        break;
      }
      case T_ALTER_USER_DEFAULT_ROLE:
      case T_SET_ROLE: {
        REGISTER_STMT_RESOLVER(AlterUserRole);
        break;
      }

      case T_GRANT_ROLE:
      case T_SYSTEM_GRANT:
      case T_GRANT: {
        REGISTER_STMT_RESOLVER(Grant);
        break;
      }
      case T_SYSTEM_REVOKE:
      case T_REVOKE:
      case T_REVOKE_ALL: {
        REGISTER_STMT_RESOLVER(Revoke);
        break;
      }
      case T_EXPLAIN: {
        REGISTER_STMT_RESOLVER(Explain);
        break;
      }
      case T_ALTER_SYSTEM_SET: {
        REGISTER_STMT_RESOLVER(AlterSystemSet);
        break;
      }
      case T_ALTER_SYSTEM_KILL: {
        REGISTER_STMT_RESOLVER(Kill);
        break;
      }
      case T_ALTER_SESSION_SET: {
        REGISTER_STMT_RESOLVER(AlterSessionSet);
        break;
      }
      case T_SET_NAMES:
        // fall through
      case T_SET_CHARSET: {
        REGISTER_STMT_RESOLVER(SetNames);
        break;
      }
      case T_KILL: {
        REGISTER_STMT_RESOLVER(Kill);
        break;
      }
      case T_EMPTY_QUERY: {
        REGISTER_STMT_RESOLVER(EmptyQuery);
        break;
      }
      case T_FLUSH_PRIVILEGES: {
        REGISTER_STMT_RESOLVER(EmptyQuery);
        break;
      }
      case T_LOCK_TABLE: {
        REGISTER_STMT_RESOLVER(LockTable);
        break;
      }
      case T_CREATE_OUTLINE: {
        REGISTER_STMT_RESOLVER(CreateOutline);
        break;
      }
      case T_ALTER_OUTLINE: {
        REGISTER_STMT_RESOLVER(AlterOutline);
        break;
      }
      case T_DROP_OUTLINE: {
        REGISTER_STMT_RESOLVER(DropOutline);
        break;
      }
      case T_SP_CREATE: {
        REGISTER_STMT_RESOLVER(CreateProcedure);
        break;
      }
      case T_SP_ALTER: {
        REGISTER_STMT_RESOLVER(AlterProcedure);
        break;
      }
      case T_SP_CALL_STMT: {
        REGISTER_STMT_RESOLVER(CallProcedure);
        break;
      }
      case T_SF_CREATE: {
        REGISTER_STMT_RESOLVER(CreateFunction);
        break;
      }
      case T_SF_ALTER: {
        REGISTER_STMT_RESOLVER(AlterFunction);
        break;
      }
      case T_SP_DROP: {
        REGISTER_STMT_RESOLVER(DropProcedure);
        break;
      }
      case T_SF_DROP: {
        REGISTER_STMT_RESOLVER(DropFunction);
        break;
      }
      case T_SP_ANONYMOUS_BLOCK: {
        REGISTER_STMT_RESOLVER(AnonymousBlock);
        break;
      }
      case T_PACKAGE_CREATE: {
        REGISTER_STMT_RESOLVER(CreatePackage);
        break;
      }
      case T_PACKAGE_CREATE_BODY: {
        REGISTER_STMT_RESOLVER(CreatePackageBody);
        break;
      }
      case T_PACKAGE_DROP: {
        REGISTER_STMT_RESOLVER(DropPackage);
        break;
      }
      case T_MYSQL_UPDATE_HISTOGRAM:
      case T_MYSQL_DROP_HISTOGRAM:
      case T_MYSQL_ANALYZE: {
        REGISTER_STMT_RESOLVER(AnalyzeStmt);
        break;
      }
      case T_LOAD_DATA_URL:
      case T_LOAD_DATA: {
        REGISTER_STMT_RESOLVER(LoadData);
        break;
      }
      case T_SET_TABLE_COMMENT:
      case T_SET_COLUMN_COMMENT: {
        REGISTER_STMT_RESOLVER(SetComment);
        break;
      }
      case T_CREATE_ROLE: {
        REGISTER_STMT_RESOLVER(CreateRole);
        break;
      }
      case T_DROP_ROLE: {
        REGISTER_STMT_RESOLVER(DropRole);
        break;
      }
      case T_ALTER_ROLE: {
        REGISTER_STMT_RESOLVER(AlterRole);
        break;
      }
      case T_CREATE_SAVEPOINT:
      case T_ROLLBACK_SAVEPOINT:
      case T_RELEASE_SAVEPOINT: {
        REGISTER_STMT_RESOLVER(SavePoint);
        break;
      }
      case T_TG_CREATE: {
        REGISTER_STMT_RESOLVER(Trigger);
        break;
      }
      case T_TG_DROP: {
        REGISTER_STMT_RESOLVER(Trigger);
        break;
      }
      case T_TG_ALTER: {
        REGISTER_STMT_RESOLVER(Trigger);
        break;
      }
      case T_DIAGNOSTICS: {
        REGISTER_STMT_RESOLVER(GetDiagnostics);
        break;
      }
      case T_ALTER_SYSTEM_RESET_PARAMETER: {
        REGISTER_STMT_RESOLVER(ResetConfig);
        break;
      }
      case T_ALTER_SYSTEM_RESET: {
        REGISTER_STMT_RESOLVER(AlterSystemReset);
        break;
      }
      default: {
        ret = OB_NOT_SUPPORTED;
        const char *type_name = get_type_name(parse_tree.type_);
        LOG_USER_ERROR(OB_NOT_SUPPORTED, "statement type");
        break;
      }
    }  // end switch
    if (OB_SUCC(ret) && stmt->is_dml_write_stmt()) {
      // todo yanli:check leader-follower database
    }
    
    if (OB_SUCC(ret) && stmt->is_dml_stmt() && !params_.session_info_->is_varparams_sql_prepare()) {
      ObDMLStmt *dml_stmt = static_cast<ObDMLStmt*>(stmt);
      ObRawExprWrapEnumSet enum_set_wrapper(*params_.expr_factory_, params_.session_info_);
      if (OB_FAIL(enum_set_wrapper.wrap_enum_set(*dml_stmt))) {
      }
    }

    if (OB_SUCC(ret) && stmt->is_dml_stmt() && !stmt->is_explain_stmt()) {
      if (OB_FAIL(params_.query_ctx_->query_hint_.init_query_hint(params_.allocator_,
                                                                  params_.session_info_,
                                                                  static_cast<ObDMLStmt*>(stmt)))) {
      } else if (OB_FAIL(params_.query_ctx_->query_hint_.check_and_set_params_from_hint(params_,
                                                         *static_cast<ObDMLStmt*>(stmt)))) {
      }
    }

    if (OB_SUCC(ret) && stmt->is_dml_stmt() && !stmt->is_explain_stmt()) {
      bool is_contain_inner_table = false;
      bool is_contain_select_for_update = false;
      ObDMLStmt *dml_stmt = static_cast<ObDMLStmt*>(stmt);
      if (OB_FAIL(dml_stmt->check_if_contain_inner_table(is_contain_inner_table))) {
      } else if (OB_FAIL(dml_stmt->check_if_contain_select_for_update(
                  is_contain_select_for_update))) {
      } else {
        params_.query_ctx_->is_contain_inner_table_ = is_contain_inner_table;
        params_.query_ctx_->is_contain_select_for_update_ = is_contain_select_for_update;
        params_.query_ctx_->has_dml_write_stmt_ = dml_stmt->is_dml_write_stmt();
      }

    }
    if (OB_SUCC(ret)) {
      stmt::StmtType stmt_type = stmt->get_stmt_type();
      if (ObStmt::is_ddl_stmt(stmt_type, stmt->has_global_variable()) || ObStmt::is_dcl_stmt(stmt_type)) {
        ObDDLStmt *ddl_stmt = static_cast<ObDDLStmt*>(stmt);
        obcall::ObDDLArg &ddl_arg = ddl_stmt->get_ddl_arg();

        if (OB_ISNULL(params_.query_ctx_)) {
          ret = OB_ERR_UNEXPECTED;
        } else {
          ddl_arg.ddl_stmt_str_ = params_.query_ctx_->get_sql_stmt();
        }
      }
    }
  }  // end if
  return ret;
}

}  // namespace sql
}  // namespace oceanbase
