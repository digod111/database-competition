/**
 * Copyright (c) 2025 OceanBase.
 *
 * Licensed under the Apache License (the "License");
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
 *
 * OB-facing helpers on top of the pure file-scan core (ob_file_scan_core.h):
 *  - path safety checks against secure_file_priv
 *  - encode/decode of a self-describing marker string that travels inside the
 *    physical plan as a constant varchar expression
 *  - schema inference for read_csv/read_json/read_ndjson/read_parquet/glob
 */

#ifndef OCEANBASE_SHARE_FILE_SQL_OB_FILE_SCAN_UTILS_H_
#define OCEANBASE_SHARE_FILE_SQL_OB_FILE_SCAN_UTILS_H_

#include "share/file_sql/ob_file_scan_core.h"
#include "lib/string/ob_string.h"
#include "common/object/ob_object.h"
#include "common/ob_accuracy.h"

namespace oceanbase
{
namespace file_sql
{

enum ObFileTableKind
{
  FT_READ_CSV = 0,
  FT_READ_JSON = 1,     // read_json with format = 'newline_delimited'
  FT_READ_NDJSON = 2,   // read_ndjson
  FT_READ_PARQUET = 3,
  FT_GLOB = 4,
  FT_INVALID = 5,
};

struct ObFileTableParam
{
  ObFileTableParam()
    : kind_(FT_READ_CSV),
      path_(),
      header_(true),
      delimiter_(','),
      quote_('\"'),
      escape_('\"')
  {}
  bool is_valid() const { return kind_ >= FT_READ_CSV && kind_ < FT_INVALID && !path_.empty(); }
  ObFileTableKind kind_;
  common::ObString path_;
  bool header_;
  char delimiter_;
  char quote_;
  char escape_;
};

struct ObFileTableCol
{
  ObFileTableCol()
    : name_(), meta_(), accuracy_(), has_null_(false)
  {}
  common::ObString name_;
  common::ObObjMeta meta_;
  common::ObAccuracy accuracy_;
  bool has_null_;  // true iff the inferred column contains at least one NULL
};

// marker format: "\x01FSTBL\x1f<kind>\x1f<path>\x1f<header>\x1f<delim>\x1f<quote>\x1f<escape>"
static const char *FILE_TABLE_MARKER_HEAD = "\x01FSTBL\x1f";
static const int64_t FILE_TABLE_MARKER_HEAD_LEN = 7;

// secure_file_priv check. secure_file_priv semantic:
//   - empty string  : file access disabled
//   - NULL in meta  : no restriction (handled by caller passing empty + flag)
// Here: secure_priv is the raw string value ("/" default, "" disables).
int check_file_path_allowed(const common::ObString &secure_priv,
                            const common::ObString &path,
                            bool &allowed);

// returns total needed length (excludes trailing zero); OB_SUCCESS on success
int encode_file_table_param(const ObFileTableParam &param,
                            char *buf,
                            const int64_t buf_len,
                            int64_t &pos);

// decode into param. path_ points into marker buffer (caller must keep it alive
// or copy it).
int decode_file_table_param(const char *buf, const int64_t len, ObFileTableParam &param);

inline bool is_file_table_marker(const char *buf, const int64_t len)
{
  return len > FILE_TABLE_MARKER_HEAD_LEN
      && 0 == MEMCMP(buf, FILE_TABLE_MARKER_HEAD, FILE_TABLE_MARKER_HEAD_LEN);
}

// infer schema:
//   CSV / NDJSON / JSONL : full streaming scan, union-widen per column
//   Parquet              : footer + column chunk statistics
//   glob                 : one VARCHAR column 'file' (matched paths, sorted)
// All strings in cols are allocated from alloc.
int infer_file_schema(const ObFileTableParam &param,
                      common::ObIAllocator &alloc,
                      common::ObIArray<ObFileTableCol> &cols);

// map a core scalar type to OB meta/accuracy
void fs_type_to_ob_meta(FSValueType vtype, common::ObObjMeta &meta, common::ObAccuracy &accuracy);

// convert a scalar string (from CSV/JSON text) to an ObObj of the column meta type.
// NULL string (len 0 and not quoted, or JSON null) yields null obj.
int fs_scalar_to_obj(FSValueType col_type,
                     const char *ptr,
                     const int64_t len,
                     const bool quoted,
                     common::ObObj &obj);

} // end namespace file_sql
} // end namespace oceanbase

#endif // OCEANBASE_SHARE_FILE_SQL_OB_FILE_SCAN_UTILS_H_
