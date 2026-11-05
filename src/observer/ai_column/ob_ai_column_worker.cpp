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

#define USING_LOG_PREFIX SERVER

#include "observer/ai_column/ob_ai_column_worker.h"
#include "common/mysqlclient/ob_mysql_proxy.h"
#include "common/mysqlclient/ob_mysql_result.h"
#include "lib/allocator/ob_arena_allocator.h"
#include "share/schema/ob_multi_version_schema_service.h"
#include "share/schema/ob_schema_getter_guard.h"
#include "share/schema/ob_table_schema.h"
#include "share/schema/ob_column_schema.h"

using namespace oceanbase::common;
using namespace oceanbase::share;
using namespace oceanbase::share::schema;

namespace oceanbase
{
namespace observer
{

ObAIColumnWorker &ai_column_worker()
{
  static ObAIColumnWorker worker;
  return worker;
}

ObAIColumnWorker::ObAIColumnWorker()
  : is_inited_(false),
    timer_(),
    sql_proxy_(nullptr),
    schema_service_(nullptr)
{
}

ObAIColumnWorker::~ObAIColumnWorker()
{
  destroy();
}

int ObAIColumnWorker::init(ObMySQLProxy *sql_proxy,
                           ObMultiVersionSchemaService *schema_service)
{
  int ret = OB_SUCCESS;
  if (is_inited_) {
    ret = OB_INIT_TWICE;
  } else if (OB_ISNULL(sql_proxy) || OB_ISNULL(schema_service)) {
    ret = OB_INVALID_ARGUMENT;
    LOG_WARN("invalid argument", K(ret), KP(sql_proxy), KP(schema_service));
  } else if (OB_FAIL(timer_.init("AIColMgr", ObMemAttr("AIColMgr")))) {
    LOG_WARN("fail to init ai column timer", K(ret));
  } else {
    sql_proxy_ = sql_proxy;
    schema_service_ = schema_service;
    disable_timeout_check();
    is_inited_ = true;
  }
  return ret;
}

int ObAIColumnWorker::start()
{
  int ret = OB_SUCCESS;
  if (!is_inited_) {
    ret = OB_NOT_INIT;
  } else if (OB_FAIL(timer_.schedule(*this, WORKER_INTERVAL_US, true))) {
    LOG_WARN("fail to schedule ai column worker", K(ret));
  } else {
    LOG_INFO("ai column worker started");
  }
  return ret;
}

void ObAIColumnWorker::stop()
{
  if (timer_.inited()) {
    timer_.stop();
  }
}

void ObAIColumnWorker::wait()
{
  if (timer_.inited()) {
    timer_.wait();
  }
}

void ObAIColumnWorker::destroy()
{
  timer_.destroy();
  is_inited_ = false;
}

void ObAIColumnWorker::runTimerTask()
{
  ObCurTraceId::init(GCTX.self_addr());
  do_work();
}

void ObAIColumnWorker::do_work()
{
  int ret = OB_SUCCESS;
  if (!is_inited_) {
    ret = OB_NOT_INIT;
  } else if (OB_FAIL(process_tables())) {
    LOG_WARN("fail to process ai columns", K(ret));
  }
}

int ObAIColumnWorker::execute_write(const ObString &sql)
{
  int ret = OB_SUCCESS;
  int64_t affected_rows = 0;
  if (OB_ISNULL(sql_proxy_)) {
    ret = OB_NOT_INIT;
  } else if (OB_FAIL(sql_proxy_->write(sql.ptr(), affected_rows))) {
    LOG_WARN("fail to execute ai column sql", K(ret), K(sql));
  }
  return ret;
}

int ObAIColumnWorker::append_quoted_ident(ObSqlString &sql, const ObString &name)
{
  int ret = OB_SUCCESS;
  if (OB_FAIL(sql.append("`"))) {
  } else if (OB_FAIL(sql.append(name))) {
  } else if (OB_FAIL(sql.append("`"))) {
  }
  return ret;
}

int ObAIColumnWorker::append_string_literal(ObSqlString &sql, const ObString &value)
{
  int ret = OB_SUCCESS;
  if (OB_FAIL(sql.append("'"))) {
  }
  for (int64_t i = 0; OB_SUCC(ret) && i < value.length(); ++i) {
    const char c = value.ptr()[i];
    if ('\'' == c) {
      ret = sql.append("''");
    } else if ('\\' == c) {
      ret = sql.append("\\\\");
    } else {
      ret = sql.append(&c, 1);
    }
  }
  if (OB_SUCC(ret)) {
    ret = sql.append("'");
  }
  return ret;
}

int ObAIColumnWorker::append_table_name(ObSqlString &sql,
                                        const ObString &db_name,
                                        const ObTableSchema &table)
{
  int ret = OB_SUCCESS;
  if (OB_FAIL(append_quoted_ident(sql, db_name))) {
  } else if (OB_FAIL(sql.append("."))) {
  } else if (OB_FAIL(append_quoted_ident(sql, table.get_table_name_str()))) {
  }
  return ret;
}

int ObAIColumnWorker::append_pending_predicate(ObSqlString &sql,
                                               const ObString &status_name)
{
  int ret = OB_SUCCESS;
  if (OB_FAIL(append_quoted_ident(sql, status_name))) {
  } else if (OB_FAIL(sql.append(" IS NOT NULL AND "))) {
  } else if (OB_FAIL(append_quoted_ident(sql, status_name))) {
  } else if (OB_FAIL(sql.append(" <> 'R' AND "))) {
  } else if (OB_FAIL(append_quoted_ident(sql, status_name))) {
  } else if (OB_FAIL(sql.append(" <> 'F'"))) {
  }
  return ret;
}

int ObAIColumnWorker::get_attempt_times(const ObString &status, int64_t &attempt_times)
{
  int ret = OB_SUCCESS;
  attempt_times = 0;
  if (status.empty()) {
    // a row inserted before the status column existed is treated as a fresh one
  } else {
    int64_t pos = 1; // skip the leading state char ('P')
    while (pos < status.length() && status[pos] >= '0' && status[pos] <= '9') {
      attempt_times = attempt_times * 10 + (status[pos] - '0');
      ++pos;
    }
  }
  return ret;
}

int ObAIColumnWorker::process_tables()
{
  int ret = OB_SUCCESS;
  ObSchemaGetterGuard guard;
  ObSEArray<uint64_t, 64> table_ids;
  if (OB_ISNULL(schema_service_)) {
    ret = OB_NOT_INIT;
  } else if (OB_FAIL(schema_service_->get_runtime_schema_guard(guard))) {
    LOG_WARN("fail to get schema guard", K(ret));
  } else if (OB_FAIL(guard.get_table_ids_in_tenant(table_ids))) {
    LOG_WARN("fail to get table ids", K(ret));
  }
  for (int64_t i = 0; OB_SUCC(ret) && i < table_ids.count(); ++i) {
    const ObTableSchema *table = nullptr;
    const ObDatabaseSchema *db_schema = nullptr;
    if (OB_FAIL(guard.get_table_schema(table_ids.at(i), table))) {
      LOG_WARN("fail to get table schema", K(ret), "table_id", table_ids.at(i));
      ret = OB_SUCCESS; // ignore this table
    } else if (OB_ISNULL(table) || table->is_sys_table() || table->is_index_tid()) {
      // do nothing
    } else if (OB_FAIL(guard.get_database_schema(table->get_database_id(), db_schema))) {
      LOG_WARN("fail to get database schema", K(ret));
      ret = OB_SUCCESS;
    } else if (OB_ISNULL(db_schema)) {
      // do nothing
    } else if (OB_FAIL(process_table(*table, db_schema->get_database_name_str()))) {
      LOG_WARN("fail to process table", K(ret), KPC(table));
      ret = OB_SUCCESS; // keep processing the other tables
    }
  }
  return ret;
}

int ObAIColumnWorker::process_table(const ObTableSchema &table, const ObString &db_name)
{
  int ret = OB_SUCCESS;
  for (ObTableSchema::const_column_iterator iter = table.column_begin();
       OB_SUCC(ret) && iter != table.column_end(); ++iter) {
    const ObColumnSchemaV2 *column = *iter;
    if (OB_ISNULL(column)) {
      ret = OB_ERR_UNEXPECTED;
    } else if (!column->is_ai_column()) {
      // do nothing
    } else if (OB_FAIL(process_ai_column(table, db_name, *column))) {
      LOG_WARN("fail to process ai column", K(ret), KPC(column));
      ret = OB_SUCCESS;
    }
  }
  return ret;
}

int ObAIColumnWorker::process_ai_column(const ObTableSchema &table,
                                        const ObString &db_name,
                                        const ObColumnSchemaV2 &ai_column)
{
  int ret = OB_SUCCESS;
  const ObColumnSchemaV2 *status_column = table.get_ai_status_column(ai_column);
  if (OB_ISNULL(status_column)) {
    LOG_WARN("ai column has no status column, skip it", K(ai_column));
  } else {
    for (int64_t i = 0; OB_SUCC(ret) && i < BATCH_SIZE; ++i) {
      bool processed = false;
      if (OB_FAIL(process_one_row(table, db_name, ai_column, *status_column, processed))) {
        LOG_WARN("fail to process ai column row", K(ret), K(ai_column));
      } else if (!processed) {
        break;
      }
    }
  }
  return ret;
}

int ObAIColumnWorker::process_one_row(const ObTableSchema &table,
                                      const ObString &db_name,
                                      const ObColumnSchemaV2 &ai_column,
                                      const ObColumnSchemaV2 &status_column,
                                      bool &processed)
{
  int ret = OB_SUCCESS;
  processed = false;
  ObArenaAllocator allocator("AIColRow");
  ObSqlString sql;
  ObSqlString rowkey_pred;
  const ObString ai_name = ai_column.get_column_name_str();
  const ObString status_name = status_column.get_column_name_str();
  const ObRowkeyInfo &rowkey_info = table.get_rowkey_info();

  // 1. find one row that is not READY / FAILED yet
  if (OB_FAIL(sql.append("SELECT "))) {
  }
  for (int64_t i = 0; OB_SUCC(ret) && i < rowkey_info.get_size(); ++i) {
    uint64_t column_id = OB_INVALID_ID;
    const ObColumnSchemaV2 *pk_column = nullptr;
    if (OB_FAIL(rowkey_info.get_column_id(i, column_id))) {
    } else if (OB_ISNULL(pk_column = table.get_column_schema(column_id))) {
      ret = OB_ERR_UNEXPECTED;
      LOG_WARN("rowkey column not found", K(ret), K(column_id));
    } else if (i > 0 && OB_FAIL(sql.append(", "))) {
    } else if (OB_FAIL(append_quoted_ident(sql, pk_column->get_column_name_str()))) {
    }
  }
  if (OB_FAIL(ret)) {
  } else if (OB_FAIL(sql.append(", "))) {
  } else if (OB_FAIL(append_quoted_ident(sql, status_name))) {
  } else if (OB_FAIL(sql.append(" FROM "))) {
  } else if (OB_FAIL(append_table_name(sql, db_name, table))) {
  } else if (OB_FAIL(sql.append(" WHERE "))) {
  } else if (OB_FAIL(append_pending_predicate(sql, status_name))) {
  } else if (OB_FAIL(sql.append(" LIMIT 1"))) {
  }

  // 2. read the rowkey and the current status of that row
  ObString status_snapshot;
  if (OB_SUCC(ret)) {
    SMART_VAR(ObMySQLProxy::MySQLResult, res) {
      ObMySQLResult *result = nullptr;
      ObString status_value;
      if (OB_FAIL(sql_proxy_->read(res, sql.ptr()))) {
        LOG_WARN("fail to read pending ai column row", K(ret), K(sql));
      } else if (OB_ISNULL(result = res.get_result())) {
        ret = OB_ERR_UNEXPECTED;
      } else if (OB_FAIL(result->next())) {
        if (OB_ITER_END == ret) {
          ret = OB_SUCCESS; // nothing pending
        } else {
          LOG_WARN("fail to fetch pending row", K(ret));
        }
      } else if (OB_FAIL(result->get_varchar(rowkey_info.get_size(), status_value))) {
        LOG_WARN("fail to get ai column status value", K(ret));
      } else if (OB_FAIL(ob_write_string(allocator, status_value, status_snapshot))) {
        LOG_WARN("fail to copy ai column status value", K(ret));
      } else {
        for (int64_t i = 0; OB_SUCC(ret) && i < rowkey_info.get_size(); ++i) {
          uint64_t column_id = OB_INVALID_ID;
          const ObColumnSchemaV2 *pk_column = nullptr;
          ObString value;
          ObString copied;
          if (OB_FAIL(rowkey_info.get_column_id(i, column_id))) {
          } else if (OB_ISNULL(pk_column = table.get_column_schema(column_id))) {
            ret = OB_ERR_UNEXPECTED;
          } else if (OB_FAIL(result->get_varchar(i, value))) {
            LOG_WARN("fail to get rowkey value", K(ret));
          } else if (OB_FAIL(ob_write_string(allocator, value, copied))) {
          } else if (i > 0 && OB_FAIL(rowkey_pred.append(" AND "))) {
          } else if (OB_FAIL(append_quoted_ident(rowkey_pred, pk_column->get_column_name_str()))) {
          } else if (OB_FAIL(rowkey_pred.append(" = "))) {
          } else if (OB_FAIL(append_string_literal(rowkey_pred, copied))) {
          }
        }
        if (OB_SUCC(ret)) {
          processed = true;
        }
      }
    }
  }

  // 3. write back the AI value, guarded by the status snapshot
  if (OB_SUCC(ret) && processed) {
    ObSqlString update;
    if (OB_FAIL(update.append("UPDATE "))) {
    } else if (OB_FAIL(append_table_name(update, db_name, table))) {
    } else if (OB_FAIL(update.append(" SET "))) {
    } else if (OB_FAIL(append_quoted_ident(update, status_name))) {
    } else if (OB_FAIL(update.append(" = 'R', "))) {
    } else if (OB_FAIL(append_quoted_ident(update, ai_name))) {
    } else if (OB_FAIL(update.append(" = ("))) {
    } else if (OB_FAIL(update.append(ai_column.get_ai_meta().expr_text_))) {
    } else if (OB_FAIL(update.append(") WHERE "))) {
    } else if (OB_FAIL(update.append(rowkey_pred))) {
    } else {
      int tmp_ret = execute_write(update.string());
      if (OB_SUCCESS == tmp_ret) {
        LOG_DEBUG("ai column value computed", K(ai_name), K(rowkey_pred));
      } else {
        // The model call failed: bump the attempt counter, or mark the row as FAILED.
        int64_t attempt_times = 0;
        ObSqlString fail_update;
        if (OB_FAIL(get_attempt_times(status_snapshot, attempt_times))) {
        } else if (OB_FAIL(fail_update.append("UPDATE "))) {
        } else if (OB_FAIL(append_table_name(fail_update, db_name, table))) {
        } else if (OB_FAIL(fail_update.append(" SET "))) {
        } else if (OB_FAIL(append_quoted_ident(fail_update, status_name))) {
        } else if (OB_FAIL(fail_update.append(" = "))) {
        }
        if (OB_SUCC(ret)) {
          if (attempt_times + 1 > ai_column.get_ai_meta().max_retries_) {
            ret = fail_update.append("'F'");
          } else {
            ret = fail_update.append_fmt("'P%ld'", attempt_times + 1);
          }
        }
        if (OB_FAIL(ret)) {
        } else if (OB_FAIL(fail_update.append(" WHERE "))) {
        } else if (OB_FAIL(fail_update.append(rowkey_pred))) {
        } else if (OB_FAIL(fail_update.append(" AND "))) {
        } else if (OB_FAIL(append_quoted_ident(fail_update, status_name))) {
        } else if (OB_FAIL(fail_update.append(" = "))) {
        } else if (OB_FAIL(append_string_literal(fail_update, status_snapshot))) {
        } else if (OB_FAIL(execute_write(fail_update.string()))) {
          LOG_WARN("fail to update ai column status", K(ret), K(fail_update.string()));
        } else {
          LOG_INFO("ai column call failed, status updated", K(ai_name), K(tmp_ret));
          ret = OB_SUCCESS;
        }
      }
    }
  }
  return ret;
}

} // namespace observer
} // namespace oceanbase
