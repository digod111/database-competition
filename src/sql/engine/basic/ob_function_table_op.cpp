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

#define USING_LOG_PREFIX SQL_ENG

#include "sql/engine/basic/ob_function_table_op.h"
#include "sql/engine/ob_exec_context.h"
#include "sql/engine/expr/ob_expr_lob_utils.h"
#include "share/file_sql/ob_file_scan_utils.h"
#include "common/json_type/ob_json_parse.h"
#include "common/json_type/ob_json_tree.h"
#include "common/timezone/ob_time_convert.h"
#include "lib/allocator/page_arena.h"
#include "lib/container/ob_array.h"

#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>


namespace oceanbase
{
using namespace common;

namespace file_sql
{

// ---------------------------------------------------------------------------
// file-as-database scan state: one instance per ObFunctionTableOp execution,
// owned by the executor and allocated from the execution context allocator.
// ---------------------------------------------------------------------------
struct CsvReadCtx
{
  int fd_;
  int64_t offset_;
  CsvReadCtx() : fd_(-1), offset_(0) {}
};

int64_t op_fd_read_fn(void *ctx, const char **buf)
{
  CsvReadCtx *fc = static_cast<CsvReadCtx *>(ctx);
  static thread_local char tls_buf[256 * 1024];
  int64_t n = pread(fc->fd_, tls_buf, sizeof(tls_buf), fc->offset_);
  if (n > 0) {
    fc->offset_ += n;
    *buf = tls_buf;
  } else {
    n = 0;
    *buf = tls_buf;
  }
  return n;
}

FSValueType fs_meta_to_fs_type(const ObObjMeta &meta)
{
  switch (meta.get_type()) {
    case ObTinyIntType: return FS_BOOLEAN;
    case ObIntType: return FS_BIGINT;
    case ObDoubleType: return FS_DOUBLE;
    case ObDateType: return FS_DATE;
    case ObDateTimeType: return FS_DATETIME;
    default: return FS_VARCHAR;
  }
}

// ---------------------------------------------------------------------------
// nested json serialization (objects / arrays inside a jsonl row keep their
// json text form in the VARCHAR column)
// ---------------------------------------------------------------------------
struct JsonSerCtx
{
  char *buf_;
  int64_t cap_;
  int64_t pos_;
  bool write_;
  JsonSerCtx() : buf_(NULL), cap_(0), pos_(0), write_(false) {}
};

int json_ser_append(JsonSerCtx &c, const char *s, int64_t len)
{
  int ret = OB_SUCCESS;
  if (c.write_) {
    if (c.pos_ + len > c.cap_) {
      ret = OB_SIZE_OVERFLOW;
    } else {
      MEMCPY(c.buf_ + c.pos_, s, len);
    }
  }
  if (OB_SUCC(ret)) {
    c.pos_ += len;
  }
  return ret;
}

int json_ser_append_char(JsonSerCtx &c, char ch)
{
  return json_ser_append(c, &ch, 1);
}

int json_ser_string(JsonSerCtx &c, const char *s, int64_t len)
{
  int ret = OB_SUCCESS;
  OZ(json_ser_append_char(c, '"'));
  for (int64_t i = 0; OB_SUCC(ret) && i < len; ++i) {
    unsigned char ch = static_cast<unsigned char>(s[i]);
    switch (ch) {
      case '"':
        OZ(json_ser_append(c, "\\\"", 2));
        break;
      case '\\':
        OZ(json_ser_append(c, "\\\\", 2));
        break;
      case '\n':
        OZ(json_ser_append(c, "\\n", 2));
        break;
      case '\r':
        OZ(json_ser_append(c, "\\r", 2));
        break;
      case '\t':
        OZ(json_ser_append(c, "\\t", 2));
        break;
      default:
        if (ch < 0x20) {
          char esc[8];
          int64_t n = snprintf(esc, sizeof(esc), "\\u%04x", ch);
          OZ(json_ser_append(c, esc, n));
        } else {
          OZ(json_ser_append_char(c, static_cast<char>(ch)));
        }
        break;
    }
  }
  if (OB_SUCC(ret)) {
    OZ(json_ser_append_char(c, '"'));
  }
  return ret;
}

int json_ser_node(ObJsonNode *node, JsonSerCtx &c);

int json_ser_container(ObJsonNode *node, JsonSerCtx &c, bool is_obj)
{
  int ret = OB_SUCCESS;
  ObJsonContainer *cont = static_cast<ObJsonContainer *>(node);
  OZ(json_ser_append_char(c, is_obj ? '{' : '['));
  const uint64_t cnt = cont->element_count();
  for (uint64_t i = 0; OB_SUCC(ret) && i < cnt; ++i) {
    if (i > 0) {
      OZ(json_ser_append_char(c, ','));
    }
    if (is_obj) {
      ObJsonObject *jo = static_cast<ObJsonObject *>(node);
      ObString key;
      if (OB_FAIL(jo->get_key(i, key))) {
        LOG_WARN("fail to get json key", K(ret));
      } else {
        OZ(json_ser_string(c, key.ptr(), key.length()));
        OZ(json_ser_append_char(c, ':'));
      }
    }
    if (OB_SUCC(ret)) {
      ObJsonNode *child = cont->get_value(i);
      if (OB_ISNULL(child)) {
        OZ(json_ser_append(c, "null", 4));
      } else {
        OZ(json_ser_node(child, c));
      }
    }
  }
  if (OB_SUCC(ret)) {
    OZ(json_ser_append_char(c, is_obj ? '}' : ']'));
  }
  return ret;
}

int json_ser_node(ObJsonNode *node, JsonSerCtx &c)
{
  int ret = OB_SUCCESS;
  switch (node->json_type()) {
    case ObJsonNodeType::J_NULL:
      OZ(json_ser_append(c, "null", 4));
      break;
    case ObJsonNodeType::J_BOOLEAN:
      OZ(json_ser_append(c,
                         static_cast<ObJsonBoolean *>(node)->value() ? "true" : "false",
                         static_cast<ObJsonBoolean *>(node)->value() ? 4 : 5));
      break;
    case ObJsonNodeType::J_INT: {
      char tmp[32];
      int64_t n = snprintf(tmp, sizeof(tmp), "%ld",
                           static_cast<long>(static_cast<ObJsonInt *>(node)->value()));
      OZ(json_ser_append(c, tmp, n));
      break;
    }
    case ObJsonNodeType::J_UINT: {
      char tmp[32];
      int64_t n = snprintf(tmp, sizeof(tmp), "%lu",
                           static_cast<unsigned long>(static_cast<ObJsonUint *>(node)->value()));
      OZ(json_ser_append(c, tmp, n));
      break;
    }
    case ObJsonNodeType::J_DOUBLE: {
      char tmp[64];
      int64_t n = snprintf(tmp, sizeof(tmp), "%.17g",
                           static_cast<ObJsonDouble *>(node)->value());
      OZ(json_ser_append(c, tmp, n));
      break;
    }
    case ObJsonNodeType::J_STRING: {
      ObString sv = static_cast<ObJsonString *>(node)->value();
      OZ(json_ser_string(c, sv.ptr(), sv.length()));
      break;
    }
    case ObJsonNodeType::J_OBJECT:
      OZ(json_ser_container(node, c, true));
      break;
    case ObJsonNodeType::J_ARRAY:
      OZ(json_ser_container(node, c, false));
      break;
    default: {
      char tmp[128];
      int64_t n = node->to_string(tmp, sizeof(tmp));
      OZ(json_ser_string(c, tmp, n));
      break;
    }
  }
  return ret;
}

// serialize any json node to compact text allocated from row_alloc
int json_node_serialize(ObJsonNode *node, ObIAllocator &row_alloc, ObString &out)
{
  int ret = OB_SUCCESS;
  JsonSerCtx c;
  c.write_ = false;
  if (OB_FAIL(json_ser_node(node, c))) {
    LOG_WARN("fail to measure json size", K(ret));
  } else if (OB_ISNULL(c.buf_ = static_cast<char *>(row_alloc.alloc(c.pos_ + 1)))) {
    ret = OB_ALLOCATE_MEMORY_FAILED;
    LOG_WARN("fail to alloc for json serialize", K(c.pos_));
  } else {
    c.cap_ = c.pos_ + 1;
    c.pos_ = 0;
    c.write_ = true;
    if (OB_FAIL(json_ser_node(node, c))) {
      LOG_WARN("fail to write json text", K(ret));
    } else {
      c.buf_[c.pos_] = '\0';
      out.assign_ptr(c.buf_, static_cast<int32_t>(c.pos_));
    }
  }
  return ret;
}

// convert one json value into an ObObj of the column's inferred type.
// string values reference row_alloc (the per-row json arena); the caller
// copies varchar datums into the execution allocator before the next row.
int json_node_to_obj(ObJsonNode *val, FSValueType col_type,
                     ObIAllocator &row_alloc, ObObj &obj)
{
  int ret = OB_SUCCESS;
  obj.reset();
  if (ObJsonNodeType::J_NULL == val->json_type()) {
    obj.set_null();
  } else if (ObJsonNodeType::J_STRING == val->json_type()) {
    ObString sv = static_cast<ObJsonString *>(val)->value();
    if (FS_VARCHAR == col_type) {
      obj.set_varchar(sv);
      obj.set_collation_level(CS_LEVEL_IMPLICIT);
      obj.set_collation_type(CS_TYPE_UTF8MB4_GENERAL_CI);
    } else {
      // string values in a typed column (date/datetime/bigint/double/boolean):
      // convert exactly like the CSV path does
      if (OB_FAIL(fs_scalar_to_obj(col_type, sv.ptr(), sv.length(), true, obj))) {
        LOG_WARN("fail to convert json string value", K(ret), K(col_type));
      }
    }
  } else if (FS_VARCHAR == col_type) {
    // typed-as-varchar column holding a non-string json value:
    // serialize the value to text so the datum type matches the column
    ObString text;
    if (OB_FAIL(json_node_serialize(val, row_alloc, text))) {
      LOG_WARN("fail to serialize json value", K(ret));
    } else {
      obj.set_varchar(text);
      obj.set_collation_level(CS_LEVEL_IMPLICIT);
      obj.set_collation_type(CS_TYPE_UTF8MB4_GENERAL_CI);
    }
  } else {
    switch (val->json_type()) {
      case ObJsonNodeType::J_BOOLEAN:
        obj.set_tinyint(static_cast<ObJsonBoolean *>(val)->value() ? 1 : 0);
        break;
      case ObJsonNodeType::J_INT: {
        int64_t v = static_cast<ObJsonInt *>(val)->value();
        if (FS_DOUBLE == col_type) {
          obj.set_double(static_cast<double>(v));
        } else {
          obj.set_int(v);
        }
        break;
      }
      case ObJsonNodeType::J_UINT: {
        uint64_t v = static_cast<ObJsonUint *>(val)->value();
        if (FS_DOUBLE == col_type || v > static_cast<uint64_t>(INT64_MAX)) {
          obj.set_double(static_cast<double>(v));
        } else {
          obj.set_int(static_cast<int64_t>(v));
        }
        break;
      }
      case ObJsonNodeType::J_DOUBLE:
        obj.set_double(static_cast<ObJsonDouble *>(val)->value());
        break;
      default: {
        // nested containers / decimal / opaque: serialize to text
        ObString text;
        if (OB_FAIL(json_node_serialize(val, row_alloc, text))) {
          LOG_WARN("fail to serialize json value", K(ret));
        } else if (FS_DOUBLE == col_type) {
          char tmp[64];
          int64_t n = text.length() < 63 ? text.length() : 63;
          MEMCPY(tmp, text.ptr(), n);
          tmp[n] = '\0';
          obj.set_double(FS_STRTOD(tmp, NULL));
        } else {
          obj.set_varchar(text);
          obj.set_collation_level(CS_LEVEL_IMPLICIT);
          obj.set_collation_type(CS_TYPE_UTF8MB4_GENERAL_CI);
        }
        break;
      }
    }
  }
  return ret;
}

int glob_path_cmp(const ObString &a, const ObString &b)
{
  int64_t n = a.length() < b.length() ? a.length() : b.length();
  int c = n > 0 ? MEMCMP(a.ptr(), b.ptr(), n) : 0;
  if (0 == c) {
    c = a.length() < b.length() ? -1 : (a.length() > b.length() ? 1 : 0);
  }
  return c;
}

struct ObFileScanState
{
public:
  explicit ObFileScanState(ObIAllocator &alloc)
    : alloc_(alloc),
      cols_(alloc),
      paths_(alloc),
      col_data_(alloc),
      csv_ctx_(),
      csv_(),
      csv_opened_(false),
      json_buf_(NULL),
      json_len_(0),
      json_pos_(0),
      json_alloc_(),
      pq_buf_(NULL),
      pq_len_(0),
      pq_arena_(),
      pq_row_(0),
      pq_loaded_rg_(-1),
      glob_idx_(0)
  {
    MEMSET(&pq_meta_, 0, sizeof(pq_meta_));
  }
  ~ObFileScanState() { close(); }

  int open();
  int reset_cursor();
  void close();

  ObIAllocator &alloc_;
  ObFileTableParam param_;
  ObArray<ObFileTableCol> cols_;
  // csv
  CsvReadCtx csv_ctx_;
  CsvScanner csv_;
  bool csv_opened_;
  // jsonl (whole-file buffer + line cursor; per-row json arena)
  char *json_buf_;
  int64_t json_len_;
  int64_t json_pos_;
  ObArenaAllocator json_alloc_;
  // parquet (whole-file buffer + footer metadata + per-rowgroup column data)
  char *pq_buf_;
  int64_t pq_len_;
  FSArena pq_arena_;
  PQFileMeta pq_meta_;
  ObArray<FSColumnData> col_data_;
  int64_t pq_row_;
  int64_t pq_loaded_rg_;
  // glob (sorted full-path list + cursor)
  ObArray<ObString> paths_;
  int64_t glob_idx_;

private:
  int open_csv();
  int close_csv();
  int open_glob();
  int read_whole(const char *path, char *&buf, int64_t &len);
};

int ObFileScanState::close_csv()
{
  if (csv_opened_) {
    csv_.destroy();
    csv_opened_ = false;
  }
  if (csv_ctx_.fd_ >= 0) {
    close(csv_ctx_.fd_);
    csv_ctx_.fd_ = -1;
  }
  return OB_SUCCESS;
}

int ObFileScanState::open_csv()
{
  int ret = OB_SUCCESS;
  OZ(close_csv());
  if (OB_SUCC(ret)) {
    csv_ctx_.offset_ = 0;
    csv_ctx_.fd_ = open(param_.path_.ptr(), O_RDONLY);
    if (csv_ctx_.fd_ < 0) {
      ret = OB_FILE_NOT_OPENED;
      LOG_WARN("fail to open csv file", K(param_.path_), K(errno));
    } else {
      int sret = csv_.init(op_fd_read_fn, &csv_ctx_,
                           param_.delimiter_, param_.quote_, param_.escape_);
      if (FS_OK != sret) {
        ret = OB_INVALID_DATA;
        LOG_WARN("fail to init csv scanner", K(sret));
      } else if (FS_OK != (sret = csv_.skip_bom())) {
        ret = OB_INVALID_DATA;
        LOG_WARN("fail to skip bom", K(sret));
      } else {
        csv_opened_ = true;
      }
    }
  }
  return ret;
}

int ObFileScanState::read_whole(const char *path, char *&buf, int64_t &len)
{
  int ret = OB_SUCCESS;
  buf = NULL;
  len = 0;
  int fd = open(path, O_RDONLY);
  if (fd < 0) {
    ret = OB_FILE_NOT_OPENED;
    LOG_WARN("fail to open file", K(path), K(errno));
  } else {
    struct stat st;
    if (0 != fstat(fd, &st)) {
      ret = OB_IO_ERROR;
      LOG_WARN("fstat failed", K(errno));
    } else {
      len = st.st_size;
      if (NULL == (buf = static_cast<char *>(alloc_.alloc(len + 1)))) {
        ret = OB_ALLOCATE_MEMORY_FAILED;
        LOG_WARN("fail to alloc file buffer", K(len));
      } else {
        int64_t off = 0;
        while (OB_SUCC(ret) && off < len) {
          int64_t n = pread(fd, buf + off, len - off, off);
          if (n <= 0) {
            ret = OB_IO_ERROR;
            LOG_WARN("pread failed", K(errno));
          } else {
            off += n;
          }
        }
        if (OB_SUCC(ret)) {
          buf[len] = '\0';
        }
      }
    }
    close(fd);
  }
  return ret;
}

int ObFileScanState::open_glob()
{
  int ret = OB_SUCCESS;
  const char *p = param_.path_.ptr();
  const int64_t plen = param_.path_.length();
  int64_t dir_end = 0;
  bool found_slash = false;
  for (int64_t i = plen - 1; i >= 0; --i) {
    if ('/' == p[i]) {
      dir_end = i + 1;
      found_slash = true;
      break;
    }
  }
  bool has_wild = false;
  for (int64_t i = 0; i < plen; ++i) {
    if ('*' == p[i] || '?' == p[i] || '[' == p[i]) {
      has_wild = true;
      break;
    }
  }
  if (!has_wild) {
    // a plain path: list the single file
    char *store = static_cast<char *>(alloc_.alloc(plen + 1));
    if (OB_ISNULL(store)) {
      ret = OB_ALLOCATE_MEMORY_FAILED;
      LOG_WARN("fail to alloc glob path");
    } else {
      MEMCPY(store, p, plen);
      store[plen] = '\0';
      ObString full(plen, store);
      OZ(paths_.push_back(full));
    }
  } else {
    char dir_buf[4096];
    const char *dir_ptr = found_slash ? p : ".";
    const int64_t dir_len = found_slash ? dir_end : 1;
    if (dir_len >= static_cast<int64_t>(sizeof(dir_buf))) {
      ret = OB_INVALID_ARGUMENT;
      LOG_WARN("glob dir too long", K(dir_len));
    } else {
      MEMCPY(dir_buf, dir_ptr, dir_len);
      dir_buf[dir_len] = '\0';
      DIR *dir = opendir(dir_buf);
      if (OB_ISNULL(dir)) {
        ret = OB_FILE_NOT_OPENED;
        LOG_WARN("fail to open dir for glob", K(dir_buf), K(errno));
      } else {
        struct dirent *ent = NULL;
        ObString pat(static_cast<int32_t>(plen - dir_end), p + dir_end);
        while (OB_SUCC(ret) && NULL != (ent = readdir(dir))) {
          if ('.' == ent->d_name[0]) {
            continue;
          }
          ObString name(strlen(ent->d_name), ent->d_name);
          bool matched = false;
          if (FS_OK != fs_glob_match(pat.ptr(), pat.length(),
                                     name.ptr(), name.length(), matched)) {
            // ignore match errors
          } else if (!matched) {
            // not matched
          } else {
            bool is_dir = false;
            if (DT_DIR == ent->d_type) {
              is_dir = true;
            } else if (DT_UNKNOWN == ent->d_type) {
              char fp[8192];
              if (dir_len + name.length() + 1 < static_cast<int64_t>(sizeof(fp))) {
                MEMCPY(fp, dir_buf, dir_len);
                MEMCPY(fp + dir_len, name.ptr(), name.length());
                fp[dir_len + name.length()] = '\0';
                struct stat st;
                if (0 == stat(fp, &st) && S_ISDIR(st.st_mode)) {
                  is_dir = true;
                }
              }
            }
            if (!is_dir) {
              // full path = dir prefix + entry name
              const int64_t prefix_len = found_slash ? dir_end : 0;
              const int64_t total = prefix_len + name.length();
              char *store = static_cast<char *>(alloc_.alloc(total + 1));
              if (OB_ISNULL(store)) {
                ret = OB_ALLOCATE_MEMORY_FAILED;
                LOG_WARN("fail to alloc glob path");
              } else {
                if (prefix_len > 0) {
                  MEMCPY(store, p, prefix_len);
                }
                MEMCPY(store + prefix_len, name.ptr(), name.length());
                store[total] = '\0';
                ObString full(total, store);
                OZ(paths_.push_back(full));
              }
            }
          }
        }
        closedir(dir);
        // sort by byte order so glob results are deterministic
        const int64_t n = paths_.count();
        for (int64_t i = 1; OB_SUCC(ret) && i < n; ++i) {
          ObString key = paths_.at(i);
          int64_t j = i - 1;
          while (j >= 0 && glob_path_cmp(paths_.at(j), key) > 0) {
            paths_.at(j + 1) = paths_.at(j);
            --j;
          }
          paths_.at(j + 1) = key;
        }
      }
    }
  }
  glob_idx_ = 0;
  return ret;
}

int ObFileScanState::open()
{
  int ret = OB_SUCCESS;
  if (OB_FAIL(infer_file_schema(param_, alloc_, cols_))) {
    LOG_WARN("fail to infer file schema", K(ret));
  } else {
    switch (param_.kind_) {
      case FT_READ_CSV:
        OZ(open_csv());
        break;
      case FT_READ_JSON:
      case FT_READ_NDJSON:
        json_pos_ = 0;
        OZ(read_whole(param_.path_.ptr(), json_buf_, json_len_));
        break;
      case FT_READ_PARQUET: {
        OZ(read_whole(param_.path_.ptr(), pq_buf_, pq_len_));
        if (OB_SUCC(ret)) {
          pq_row_ = 0;
          pq_loaded_rg_ = -1;
          if (pq_len_ < 12 || 0 != MEMCMP(pq_buf_ + pq_len_ - 4, "PAR1", 4)) {
            ret = OB_INVALID_DATA;
            LOG_WARN("not a parquet file", K(param_.path_));
          } else if (FS_OK != pq_arena_.init(4 << 20)) {
            ret = OB_ALLOCATE_MEMORY_FAILED;
            LOG_WARN("fail to init parquet arena");
          } else if (FS_OK != pq_parse_footer(pq_buf_, pq_len_, pq_arena_, pq_meta_)) {
            ret = OB_INVALID_DATA;
            LOG_WARN("fail to parse parquet footer", K(param_.path_));
          } else if (pq_meta_.column_count != cols_.count()) {
            ret = OB_ERR_UNEXPECTED;
            LOG_WARN("parquet column count mismatch",
                     K(pq_meta_.column_count), K(cols_.count()));
          }
        }
        break;
      }
      case FT_GLOB:
        OZ(open_glob());
        break;
      default:
        ret = OB_INVALID_ARGUMENT;
        LOG_WARN("bad file table kind", K(param_.kind_));
        break;
    }
  }
  return ret;
}

int ObFileScanState::reset_cursor()
{
  int ret = OB_SUCCESS;
  switch (param_.kind_) {
    case FT_READ_CSV:
      OZ(open_csv());
      break;
    case FT_READ_JSON:
    case FT_READ_NDJSON:
      json_pos_ = 0;
      break;
    case FT_READ_PARQUET:
      pq_row_ = 0;
      pq_loaded_rg_ = -1;
      break;
    case FT_GLOB:
      glob_idx_ = 0;
      break;
    default:
      ret = OB_INVALID_ARGUMENT;
      break;
  }
  return ret;
}

void ObFileScanState::close()
{
  close_csv();
  json_buf_ = NULL;
  json_len_ = 0;
  json_pos_ = 0;
  json_alloc_.reset();
  pq_buf_ = NULL;
  pq_len_ = 0;
  col_data_.reset();
  pq_row_ = 0;
  pq_loaded_rg_ = -1;
  paths_.reset();
  glob_idx_ = 0;
}

} // end namespace file_sql

namespace sql
{

OB_SERIALIZE_MEMBER((ObFunctionTableSpec, ObOpSpec), value_expr_, column_exprs_, has_correlated_expr_);

int ObFunctionTableOp::inner_open()
{
  int ret = OB_SUCCESS;
  node_idx_ = 0;
  already_calc_ = false;
  if (OB_ISNULL(MY_SPEC.value_expr_)) {
    ret = OB_ERR_UNEXPECTED;
  } else if (ObExtendType == MY_SPEC.value_expr_->datum_meta_.type_) {
    next_row_func_ = &ObFunctionTableOp::inner_get_next_row_udf;
  } else if (T_CONST_EXPR == MY_SPEC.value_expr_->type_) {
    // a constant varchar value expr may be a file table marker
    // ("\x01FSTBL\x1f..." produced by the resolver for read_csv and friends)
    ObDatum *value = NULL;
    if (OB_FAIL(MY_SPEC.value_expr_->eval(eval_ctx_, value))) {
      LOG_WARN("fail to eval table function value", K(ret));
    } else if (OB_NOT_NULL(value) && !value->is_null()
               && file_sql::is_file_table_marker(value->get_string().ptr(),
                                                 value->get_string().length())) {
      next_row_func_ = &ObFunctionTableOp::inner_get_next_row_file;
      OZ(open_file_scan(value->get_string()));
    } else {
      next_row_func_ = &ObFunctionTableOp::inner_get_next_row_sys_func;
    }
  } else {
    next_row_func_ = &ObFunctionTableOp::inner_get_next_row_sys_func;
  }
  return ret;
}

int ObFunctionTableOp::inner_rescan()
{
  int ret = OB_SUCCESS;
  if (OB_FAIL(ObOperator::inner_rescan())) {
  } else {
    node_idx_ = 0;
    if (MY_SPEC.has_correlated_expr_) {
      row_count_ = 0;
      col_count_ = 0;
      value_table_ = NULL;
      already_calc_ = false;
    }
    if (OB_SUCC(ret) && OB_NOT_NULL(fs_state_)) {
      OZ(fs_state_->reset_cursor());
    }
  }
  return ret;
}

int ObFunctionTableOp::inner_close()
{
  int ret = OB_SUCCESS;
  node_idx_ = 0;
  row_count_ = 0;
  col_count_ = 0;
  value_table_ = NULL;
  close_file_scan();
  return ret;
}

void ObFunctionTableOp::destroy()
{
  close_file_scan();
  ObOperator::destroy();
}


int ObFunctionTableOp::get_current_result(ObObj &result)
{
  int ret = OB_SUCCESS;
  void *data = NULL;
  CK (already_calc_);
  if (node_idx_ < 0 || node_idx_ >= row_count_ * col_count_) {
    ret = OB_ERR_UNEXPECTED;
    LOG_WARN("fail to get current result in table function",
              K(node_idx_), K(row_count_), K(col_count_));
  }
  do {
    CK (node_idx_ >= 0);
    if (OB_SUCC(ret) && node_idx_ >= row_count_) {
      ret = OB_ITER_END;
    }
    CK (OB_NOT_NULL(value_table_));
    OX (data = value_table_->get_data());
    CK (OB_NOT_NULL(data));
    OX (result = (static_cast<ObObj*>(data))[node_idx_++]);
  } while (OB_SUCC(ret) && result.get_meta().get_type() == ObMaxType);
  return ret;
}

int ObFunctionTableOp::inner_get_next_row()
{
  return (this->*next_row_func_)();
}

int ObFunctionTableOp::inner_get_next_row_udf()
{
  int ret = OB_SUCCESS;
  ObPhysicalPlanCtx *plan_ctx = nullptr;
  clear_evaluated_flag();
  if (OB_ISNULL(plan_ctx = ctx_.get_physical_plan_ctx())) {
    ret = OB_ERR_UNEXPECTED;
  } else if (OB_ISNULL(MY_SPEC.value_expr_)) {
    ret = OB_ERR_UNEXPECTED;
  } else if (ObExtendType != MY_SPEC.value_expr_->datum_meta_.type_) {
    ret = OB_ERR_UNEXPECTED;
  } else if (FALSE_IT(plan_ctx->set_autoinc_id_tmp(0))) {
  } else if (OB_FAIL(ctx_.check_status())) {
  } else {
    ObDatum *value = nullptr;
    if (!already_calc_) {
      if (OB_FAIL(MY_SPEC.value_expr_->eval(eval_ctx_, value))) {
      } else if (value->is_null()) {
        //do nothing
      } else if (OB_ISNULL(value_table_ 
                 = reinterpret_cast<pl::ObPLCollection*>(value->get_ext()))) {
        ret = OB_ERR_UNEXPECTED;
      } else {
        row_count_ = value_table_->is_inited() ? value_table_->get_count() : 0;
        col_count_ = value_table_->get_column_count();
      }
      already_calc_ = true;
    }
    if (OB_SUCC(ret) && OB_UNLIKELY(node_idx_ >= row_count_)) {
      ret = OB_ITER_END;
    }
    CK (MY_SPEC.column_exprs_.count() >= col_count_);
    ObObj obj_stack[col_count_];
    if (OB_SUCC(ret)) {
      if (nullptr != value_table_ 
          && ObExtendType == value_table_->get_element_type().get_obj_type()) {
        pl::ObPLComposite *composite = NULL;
        pl::ObPLRecord *record = NULL;
        ObObj record_obj;
        OZ (get_current_result(record_obj));
        if (OB_FAIL(ret)) {
        } else if (ObUserDefinedSQLType == record_obj.get_type()) {
          obj_stack[0] = record_obj;   
        } else if (record_obj.is_pl_extend()) {
          CK (OB_NOT_NULL(composite = reinterpret_cast<pl::ObPLComposite*>(record_obj.get_ext())));
          if (OB_SUCC(ret)) {
            if (composite->is_record()) {
              OX (record = static_cast<pl::ObPLRecord*>(composite));
              CK (record->get_count() == col_count_);
              for (int64_t i = 0; OB_SUCC(ret) && i < col_count_; ++i) {
                OZ (record->get_element(i, obj_stack[i]));
              }
            } else if (composite->is_collection()) {
              OX (obj_stack[0] = record_obj);
            } else {
              ret = OB_ERR_UNEXPECTED;
            }
          }
        } else {
          ret = OB_ERR_UNEXPECTED;
          LOG_ERROR("unexpected here", K(ret), K(record_obj), K(record_obj.meta_));
        }
      } else {
        for (int64_t i = 0; OB_SUCC(ret) && i < col_count_; ++i) {
          if (OB_FAIL(get_current_result(obj_stack[i]))) {
          }
        }
      }
    }
    if (OB_SUCC(ret)) {
      for (int64_t i = 0; OB_SUCC(ret) && i < col_count_; ++i) {
        if (obj_stack[i].is_null()) {
          MY_SPEC.column_exprs_.at(i)->locate_datum_for_write(eval_ctx_).set_null();
        } else {
          const ObObjDatumMapType &datum_map = MY_SPEC.column_exprs_.at(i)->obj_datum_map_;
          ObExpr * const &expr = MY_SPEC.column_exprs_.at(i);
          ObDatum &datum = expr->locate_datum_for_write(eval_ctx_);
          if (OB_FAIL(datum.from_obj(obj_stack[i], datum_map))) {
          } else if (is_lob_storage(obj_stack[i].get_type()) &&
                     OB_FAIL(ob_adjust_lob_datum(get_exec_ctx(), obj_stack[i],
                                                 expr->obj_meta_, datum_map,
                                                 get_exec_ctx().get_allocator(), datum))) {
          }
        }
        if (OB_SUCC(ret)) {
          MY_SPEC.column_exprs_.at(i)->set_evaluated_projected(eval_ctx_);
        }
      }
    }
  }
  return ret;
}

int ObFunctionTableOp::inner_get_next_row_sys_func()
{
  int ret = OB_SUCCESS;
  ObPhysicalPlanCtx *plan_ctx = nullptr;
  ObDatum *value = nullptr;
  clear_evaluated_flag();
  if (OB_ISNULL(plan_ctx = ctx_.get_physical_plan_ctx())) {
    ret = OB_ERR_UNEXPECTED;
  } else if (OB_FAIL(ctx_.check_status())) {
  } else if (OB_FAIL(MY_SPEC.value_expr_->eval(eval_ctx_, value))) {
    if (OB_ITER_END != ret) {
    }
  } else {
    MY_SPEC.column_exprs_.at(0)->locate_datum_for_write(eval_ctx_).set_datum(*value);
    MY_SPEC.column_exprs_.at(0)->set_evaluated_projected(eval_ctx_);
  }
  return ret;
}

int ObFunctionTableOp::open_file_scan(const ObString &marker)
{
  int ret = OB_SUCCESS;
  ObIAllocator &alloc = ctx_.get_allocator();
  void *buf = alloc.alloc(sizeof(file_sql::ObFileScanState));
  if (OB_ISNULL(buf)) {
    ret = OB_ALLOCATE_MEMORY_FAILED;
    LOG_WARN("fail to alloc file scan state");
  } else {
    fs_state_ = new (buf) file_sql::ObFileScanState(alloc);
    file_sql::ObFileScanState *st = static_cast<file_sql::ObFileScanState *>(fs_state_);
    if (OB_FAIL(st->open())) {
      LOG_WARN("fail to open file scan", K(ret),
               K(st->param_.kind_), K(st->param_.path_));
    } else if (st->cols_.count() != MY_SPEC.column_exprs_.count()) {
      ret = OB_ERR_UNEXPECTED;
      LOG_WARN("file table column count mismatch", K(st->cols_.count()),
               K(MY_SPEC.column_exprs_.count()));
    }
  }
  return ret;
}

void ObFunctionTableOp::close_file_scan()
{
  if (OB_NOT_NULL(fs_state_)) {
    fs_state_->~ObFileScanState();
    ctx_.get_allocator().free(fs_state_);
    fs_state_ = NULL;
  }
}

int ObFunctionTableOp::inner_get_next_row_file()
{
  int ret = OB_SUCCESS;
  clear_evaluated_flag();
  if (OB_ISNULL(fs_state_)) {
    ret = OB_ERR_UNEXPECTED;
  } else if (OB_FAIL(ctx_.check_status())) {
  } else {
    const int64_t ncol = MY_SPEC.column_exprs_.count();
    if (ncol <= 0 || ncol != fs_state_->cols_.count()) {
      ret = OB_ERR_UNEXPECTED;
      LOG_WARN("file table column count mismatch", K(ncol),
               K(fs_state_->cols_.count()));
    } else {
      ObObj objs_stack[ncol];
      switch (fs_state_->param_.kind_) {
        case file_sql::FT_READ_CSV:
          OZ(next_file_row_csv(objs_stack));
          break;
        case file_sql::FT_READ_JSON:
        case file_sql::FT_READ_NDJSON:
          OZ(next_file_row_json(objs_stack));
          break;
        case file_sql::FT_READ_PARQUET:
          OZ(next_file_row_parquet(objs_stack));
          break;
        case file_sql::FT_GLOB:
          OZ(next_file_row_glob(objs_stack));
          break;
        default:
          ret = OB_ERR_UNEXPECTED;
          LOG_WARN("bad file table kind", K(fs_state_->param_.kind_));
          break;
      }
      if (OB_SUCC(ret)) {
        OZ(fill_file_row(objs_stack, ncol));
      }
    }
  }
  return ret;
}

int ObFunctionTableOp::next_file_row_csv(ObObj *objs)
{
  int ret = OB_SUCCESS;
  file_sql::ObFileScanState &st = *fs_state_;
  static const int32_t MAX_FILE_COLS = 1024;
  file_sql::CSVField fields[MAX_FILE_COLS];
  int32_t nfield = 0;
  int sret = st.csv_.next_record(fields, MAX_FILE_COLS, nfield);
  if (file_sql::FS_ERR_EOF == sret) {
    ret = OB_ITER_END;
  } else if (file_sql::FS_OK != sret) {
    ret = OB_INVALID_DATA;
    LOG_WARN("fail to scan csv record", K(sret), K(st.param_.path_));
  } else {
    for (int64_t i = 0; OB_SUCC(ret) && i < st.cols_.count(); ++i) {
      if (i < nfield) {
        if (OB_FAIL(file_sql::fs_scalar_to_obj(
                        file_sql::fs_meta_to_fs_type(st.cols_.at(i).meta_),
                        fields[i].ptr, fields[i].len, fields[i].quoted, objs[i]))) {
          LOG_WARN("fail to convert csv field", K(ret), K(i));
        }
      } else {
        // missing trailing fields count as NULL (same as inference)
        objs[i].set_null();
      }
    }
  }
  return ret;
}

int ObFunctionTableOp::next_file_row_json(ObObj *objs)
{
  int ret = OB_SUCCESS;
  file_sql::ObFileScanState &st = *fs_state_;
  bool produced = false;
  while (OB_SUCC(ret) && !produced && st.json_pos_ < st.json_len_) {
    const int64_t ls = st.json_pos_;
    int64_t le = ls;
    while (le < st.json_len_ && '\n' != st.json_buf_[le]) {
      ++le;
    }
    st.json_pos_ = le + 1;
    int64_t len = le - ls;
    // trim trailing \r / blanks
    while (len > 0 && ('\r' == st.json_buf_[ls + len - 1]
                       || ' ' == st.json_buf_[ls + len - 1]
                       || '\t' == st.json_buf_[ls + len - 1])) {
      --len;
    }
    int64_t s = ls;
    while (len > 0 && (' ' == st.json_buf_[s] || '\t' == st.json_buf_[s])) {
      ++s;
      --len;
    }
    if (len <= 0) {
      continue;
    }
    // per-row json arena: freed on the next row, after datums are copied
    st.json_alloc_.reset();
    ObString line(static_cast<int32_t>(len), st.json_buf_ + s);
    ObJsonNode *node = NULL;
    if (OB_FAIL(ObJsonParser::get_tree(&st.json_alloc_, line, node))) {
      ret = OB_INVALID_DATA;
      LOG_WARN("fail to parse json line", K(ret));
    } else if (OB_ISNULL(node) || ObJsonNodeType::J_OBJECT != node->json_type()) {
      ret = OB_INVALID_DATA;
      LOG_WARN("jsonl line is not a json object");
    } else {
      ObJsonObject *jo = static_cast<ObJsonObject *>(node);
      for (int64_t i = 0; OB_SUCC(ret) && i < st.cols_.count(); ++i) {
        ObJsonNode *val = NULL;
        for (uint64_t k = 0; OB_SUCC(ret) && k < jo->element_count(); ++k) {
          ObString key;
          if (OB_FAIL(jo->get_key(k, key))) {
            LOG_WARN("fail to get json key", K(ret));
          } else if (0 == key.case_compare(st.cols_.at(i).name_)) {
            val = jo->get_value(k);
            break;
          }
        }
        if (OB_FAIL(ret)) {
        } else if (OB_ISNULL(val) || ObJsonNodeType::J_NULL == val->json_type()) {
          objs[i].set_null();
        } else if (OB_FAIL(file_sql::json_node_to_obj(
                           val,
                           file_sql::fs_meta_to_fs_type(st.cols_.at(i).meta_),
                           st.json_alloc_, objs[i]))) {
          LOG_WARN("fail to convert json value", K(ret), K(i));
        }
      }
      if (OB_SUCC(ret)) {
        produced = true;
      }
    }
  }
  if (OB_SUCC(ret) && !produced) {
    ret = OB_ITER_END;
  }
  return ret;
}

int ObFunctionTableOp::next_file_row_parquet(ObObj *objs)
{
  int ret = OB_SUCCESS;
  file_sql::ObFileScanState &st = *fs_state_;
  if (st.pq_row_ >= st.pq_meta_.num_rows) {
    ret = OB_ITER_END;
  } else {
    int32_t rg_idx = -1;
    for (int32_t i = 0; OB_SUCC(ret) && i < st.pq_meta_.row_group_count; ++i) {
      const file_sql::PQRowGroup &rg = st.pq_meta_.row_groups[i];
      if (st.pq_row_ >= rg.base_row && st.pq_row_ < rg.base_row + rg.num_rows) {
        rg_idx = i;
        break;
      }
    }
    if (rg_idx < 0) {
      ret = OB_ERR_UNEXPECTED;
      LOG_WARN("row not found in any row group", K(st.pq_row_));
    } else {
      file_sql::PQRowGroup &rg = st.pq_meta_.row_groups[rg_idx];
      if (rg_idx != st.pq_loaded_rg_) {
        // lazily decode all column chunks of the current row group
        st.col_data_.reset();
        for (int32_t c = 0; OB_SUCC(ret) && c < st.pq_meta_.column_count; ++c) {
          file_sql::PQColumnChunk chunk;
          MEMSET(&chunk, 0, sizeof(chunk));
          if (file_sql::FS_OK != file_sql::pq_find_chunk(rg, c, chunk)) {
            ret = OB_INVALID_DATA;
            LOG_WARN("fail to find column chunk", K(c));
          } else {
            file_sql::FSColumnData cd;
            if (file_sql::FS_OK != file_sql::pq_read_column_chunk(
                    st.pq_buf_, st.pq_len_, st.pq_meta_.columns[c], chunk,
                    st.pq_arena_, cd)) {
              ret = OB_INVALID_DATA;
              LOG_WARN("fail to read column chunk", K(c));
            } else if (OB_FAIL(st.col_data_.push_back(cd))) {
              ret = OB_ALLOCATE_MEMORY_FAILED;
              LOG_WARN("fail to store column data", K(ret));
            }
          }
        }
        if (OB_SUCC(ret)) {
          st.pq_loaded_rg_ = rg_idx;
        }
      }
      if (OB_SUCC(ret)) {
        const int64_t local = st.pq_row_ - rg.base_row;
        for (int64_t c = 0; OB_SUCC(ret) && c < st.pq_meta_.column_count; ++c) {
          const file_sql::FSColumnData &cd = st.col_data_.at(c);
          if (local >= cd.count) {
            ret = OB_ERR_UNEXPECTED;
            LOG_WARN("row beyond column data", K(local), K(cd.count));
          } else if (0 == cd.not_null[local]) {
            objs[c].set_null();
          } else {
            switch (cd.value_type) {
              case file_sql::FS_BOOLEAN:
                objs[c].set_tinyint(cd.bools[local] ? 1 : 0);
                break;
              case file_sql::FS_BIGINT:
                objs[c].set_int(cd.ints[local]);
                break;
              case file_sql::FS_DOUBLE:
                objs[c].set_double(cd.dbls[local]);
                break;
              case file_sql::FS_VARCHAR:
                objs[c].set_varchar(ObString(cd.strs[local].len, cd.strs[local].ptr));
                objs[c].set_collation_level(CS_LEVEL_IMPLICIT);
                objs[c].set_collation_type(CS_TYPE_UTF8MB4_GENERAL_CI);
                break;
              case file_sql::FS_DATE: {
                char dbuf[16];
                int64_t dl = 0;
                if (file_sql::FS_OK != file_sql::fs_days_to_date_str(
                        cd.ints[local], dbuf, sizeof(dbuf), dl)) {
                  ret = OB_INVALID_DATA;
                  LOG_WARN("bad parquet date value", K(cd.ints[local]));
                } else {
                  int32_t dv = 0;
                  if (OB_FAIL(ObTimeConverter::str_to_date(
                          ObString(static_cast<int32_t>(dl), dbuf), dv))) {
                    LOG_WARN("bad parquet date string", K(dbuf));
                  } else {
                    objs[c].set_date(dv);
                  }
                }
                break;
              }
              case file_sql::FS_DATETIME: {
                const file_sql::PQColumn &pc = st.pq_meta_.columns[c];
                const bool is_micros = (file_sql::PQ_INT96 == pc.physical_type)
                                       || (file_sql::PQC_TIMESTAMP_MICROS
                                           == pc.converted_type);
                const int64_t millis = is_micros ? cd.ints[local] / 1000
                                                 : cd.ints[local];
                char tbuf[32];
                int64_t tl = 0;
                if (file_sql::FS_OK != file_sql::fs_millis_to_datetime_str(
                        millis, tbuf, sizeof(tbuf), tl)) {
                  ret = OB_INVALID_DATA;
                  LOG_WARN("bad parquet datetime value", K(cd.ints[local]));
                } else {
                  ObTimeConvertCtx cvrt_ctx(NULL, false /* not timestamp */);
                  int64_t tv = 0;
                  if (OB_FAIL(ObTimeConverter::str_to_datetime(
                          ObString(static_cast<int32_t>(tl), tbuf), cvrt_ctx, tv))) {
                    LOG_WARN("bad parquet datetime string", K(tbuf));
                  } else {
                    objs[c].set_datetime(tv);
                  }
                }
                break;
              }
              default:
                objs[c].set_null();
                break;
            }
          }
        }
        if (OB_SUCC(ret)) {
          ++st.pq_row_;
        }
      }
    }
  }
  return ret;
}

int ObFunctionTableOp::next_file_row_glob(ObObj *objs)
{
  int ret = OB_SUCCESS;
  file_sql::ObFileScanState &st = *fs_state_;
  if (st.glob_idx_ >= st.paths_.count()) {
    ret = OB_ITER_END;
  } else {
    objs[0].set_varchar(st.paths_.at(st.glob_idx_++));
    objs[0].set_collation_level(CS_LEVEL_IMPLICIT);
    objs[0].set_collation_type(CS_TYPE_UTF8MB4_GENERAL_CI);
  }
  return ret;
}

int ObFunctionTableOp::fill_file_row(ObObj *objs, const int64_t ncol)
{
  int ret = OB_SUCCESS;
  for (int64_t i = 0; OB_SUCC(ret) && i < ncol; ++i) {
    ObExpr *expr = MY_SPEC.column_exprs_.at(i);
    if (OB_ISNULL(expr)) {
      ret = OB_ERR_UNEXPECTED;
      LOG_WARN("null column expr", K(i));
    } else {
      ObDatum &datum = expr->locate_datum_for_write(eval_ctx_);
      if (objs[i].is_null()) {
        datum.set_null();
      } else if (ObVarcharType == objs[i].get_type()) {
        // varchar datum is shallow: copy the buffer into the execution
        // allocator (source memory may be a file buffer or per-row arena)
        const ObString sv = objs[i].get_varchar();
        char *store = static_cast<char *>(ctx_.get_allocator().alloc(sv.length() + 1));
        if (OB_ISNULL(store)) {
          ret = OB_ALLOCATE_MEMORY_FAILED;
          LOG_WARN("fail to alloc varchar datum", K(sv.length()));
        } else {
          MEMCPY(store, sv.ptr(), sv.length());
          store[sv.length()] = '\0';
          datum.set_string(ObString(static_cast<int32_t>(sv.length()), store));
        }
      } else {
        OZ(datum.from_obj(objs[i], expr->obj_datum_map_));
      }
      if (OB_SUCC(ret)) {
        expr->set_evaluated_projected(eval_ctx_);
      }
    }
  }
  return ret;
}


} // end namespace sql
} // end namespace oceanbase
