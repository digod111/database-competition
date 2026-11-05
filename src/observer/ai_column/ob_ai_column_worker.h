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

#ifndef OCEANBASE_OBSERVER_AI_COLUMN_OB_AI_COLUMN_WORKER_H_
#define OCEANBASE_OBSERVER_AI_COLUMN_OB_AI_COLUMN_WORKER_H_

#include "lib/task/ob_timer.h"
#include "lib/string/ob_sql_string.h"
#include "share/ob_define.h"

namespace oceanbase
{
namespace common
{
class ObMySQLProxy;
}
namespace share
{
class ObMultiVersionSchemaService;
namespace schema
{
class ObTableSchema;
class ObColumnSchemaV2;
}
}
namespace observer
{

// Background worker of AI COLUMN.
//
// Rows whose hidden AI status column is neither READY ('R') nor FAILED ('F')
// are pending: the worker evaluates the AI expression captured in the column
// schema, writes the result back into the AI result column and marks the row
// as READY in a single guarded update. If the model call fails, the attempt
// counter carried by the status value is bumped, and the row turns into
// FAILED once `max_retries` is exhausted.
class ObAIColumnWorker : public common::ObTimerTask
{
public:
  static const int64_t WORKER_INTERVAL_US = 2 * 1000L * 1000L; // 2s
  static const int64_t BATCH_SIZE = 32;

  ObAIColumnWorker();
  virtual ~ObAIColumnWorker();

  int init(common::ObMySQLProxy *sql_proxy,
           share::ObMultiVersionSchemaService *schema_service);
  int start();
  void stop();
  void wait();
  void destroy();
  bool is_inited() const { return is_inited_; }
  virtual void runTimerTask() override;
  void do_work();

private:
  int process_tables();
  int process_table(const share::schema::ObTableSchema &table,
                    const common::ObString &db_name);
  int process_ai_column(const share::schema::ObTableSchema &table,
                        const common::ObString &db_name,
                        const share::schema::ObColumnSchemaV2 &ai_column);
  // Process at most one pending row of `ai_column`.
  // `processed` is false when there is no pending row left.
  int process_one_row(const share::schema::ObTableSchema &table,
                      const common::ObString &db_name,
                      const share::schema::ObColumnSchemaV2 &ai_column,
                      const share::schema::ObColumnSchemaV2 &status_column,
                      bool &processed);
  int execute_write(const common::ObString &sql);

  static int append_quoted_ident(common::ObSqlString &sql, const common::ObString &name);
  static int append_string_literal(common::ObSqlString &sql, const common::ObString &value);
  static int append_table_name(common::ObSqlString &sql,
                               const common::ObString &db_name,
                               const share::schema::ObTableSchema &table);
  static int append_pending_predicate(common::ObSqlString &sql, const common::ObString &status_name);
  static int get_attempt_times(const common::ObString &status, int64_t &attempt_times);

private:
  bool is_inited_;
  common::ObTimer timer_;
  common::ObMySQLProxy *sql_proxy_;
  share::ObMultiVersionSchemaService *schema_service_;
};

// Process wide AI COLUMN worker instance, started by ObServer.
ObAIColumnWorker &ai_column_worker();

} // namespace observer
} // namespace oceanbase

#endif // OCEANBASE_OBSERVER_AI_COLUMN_OB_AI_COLUMN_WORKER_H_
