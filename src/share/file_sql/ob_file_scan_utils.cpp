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
 */

#define USING_LOG_PREFIX SHARE

#include "share/file_sql/ob_file_scan_utils.h"
#include "common/timezone/ob_time_convert.h"
#include "common/json_type/ob_json_parse.h"
#include "common/json_type/ob_json_tree.h"
#include "lib/allocator/ob_allocator.h"
#include "lib/utility/ob_macro_utils.h"
#include "lib/ob_check_macros.h"

#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <dirent.h>

using namespace oceanbase::common;

namespace oceanbase
{
namespace file_sql
{

int check_file_path_allowed(const ObString &secure_priv,
                            const ObString &path,
                            bool &allowed)
{
  int ret = OB_SUCCESS;
  allowed = false;
  if (path.empty()) {
    ret = OB_INVALID_ARGUMENT;
    LOG_WARN("empty file path", K(path));
  } else if (ObString::make_string("NULL") == secure_priv
             || ObString::make_string("null") == secure_priv) {
    // secure_file_priv = NULL disables import/export
    allowed = false;
  } else if (secure_priv.empty()) {
    // empty string means no restriction
    allowed = true;
  } else {
    // path must be under secure_priv directory
    const int64_t plen = secure_priv.length();
    if (path.length() < plen
        || 0 != MEMCMP(path.ptr(), secure_priv.ptr(), plen)) {
      allowed = false;
    } else if (1 == plen && '/' == secure_priv.ptr()[0]) {
      allowed = true;
    } else {
      // allow exact dir or anything below it; require '/' boundary
      allowed = ('/' == path.ptr()[plen - 1]) || (plen < path.length() && '/' == path.ptr()[plen]);
    }
  }
  return ret;
}

int encode_file_table_param(const ObFileTableParam &param,
                            char *buf,
                            const int64_t buf_len,
                            int64_t &pos)
{
  int ret = OB_SUCCESS;
  pos = 0;
  if (OB_ISNULL(buf) || buf_len <= 0 || !param.is_valid()) {
    ret = OB_INVALID_ARGUMENT;
    LOG_WARN("invalid arg to encode file table param", K(buf_len), K(param.kind_));
  } else {
    int64_t need = FILE_TABLE_MARKER_HEAD_LEN + 32 + param.path_.length() + 32;
    if (need > buf_len) {
      ret = OB_SIZE_OVERFLOW;
      LOG_WARN("buffer too small for file table marker", K(need), K(buf_len));
    } else {
      MEMCPY(buf + pos, FILE_TABLE_MARKER_HEAD, FILE_TABLE_MARKER_HEAD_LEN);
      pos += FILE_TABLE_MARKER_HEAD_LEN;
      pos += snprintf(buf + pos, buf_len - pos, "%d\x1f", static_cast<int>(param.kind_));
      MEMCPY(buf + pos, param.path_.ptr(), param.path_.length());
      pos += param.path_.length();
      pos += snprintf(buf + pos, buf_len - pos,
                      "\x1f%d\x1f%c\x1f%c\x1f%c",
                      param.header_ ? 1 : 0,
                      param.delimiter_, param.quote_, param.escape_);
    }
  }
  return ret;
}

int decode_file_table_param(const char *buf, const int64_t len, ObFileTableParam &param)
{
  int ret = OB_SUCCESS;
  param = ObFileTableParam();
  if (OB_ISNULL(buf) || !is_file_table_marker(buf, len)) {
    ret = OB_INVALID_ARGUMENT;
    LOG_WARN("invalid file table marker", K(len));
  } else {
    const char *p = buf + FILE_TABLE_MARKER_HEAD_LEN;
    const char *end = buf + len;
    // kind
    char *next = NULL;
    long kind = strtol(p, &next, 10);
    if (next == p || next >= end || '\x1f' != *next) {
      ret = OB_INVALID_ARGUMENT;
      LOG_WARN("bad kind in file table marker");
    } else {
      p = next + 1;
      // path until next \x1f
      const char *sep = static_cast<const char *>(memchr(p, '\x1f', end - p));
      if (OB_ISNULL(sep)) {
        ret = OB_INVALID_ARGUMENT;
        LOG_WARN("bad path in file table marker");
      } else {
        param.kind_ = static_cast<ObFileTableKind>(kind);
        param.path_.assign_ptr(const_cast<char *>(p), static_cast<int32_t>(sep - p));
        p = sep + 1;
        // header
        param.header_ = (*p != '0');
        if (p >= end || '\x1f' != *(p + 1)) {
          ret = OB_INVALID_ARGUMENT;
        } else {
          p += 2;
          param.delimiter_ = *p;
          if (p + 1 >= end || '\x1f' != *(p + 1)) {
            ret = OB_INVALID_ARGUMENT;
          } else {
            p += 2;
            param.quote_ = *p;
            if (p + 1 >= end || '\x1f' != *(p + 1)) {
              ret = OB_INVALID_ARGUMENT;
            } else {
              p += 2;
              param.escape_ = *p;
            }
          }
        }
      }
    }
    if (OB_FAIL(ret)) {
      LOG_WARN("bad file table marker tail");
    }
  }
  return ret;
}

void fs_type_to_ob_meta(FSValueType vtype, ObObjMeta &meta, ObAccuracy &accuracy)
{
  ObObjType ob_type = ObVarcharType;
  switch (vtype) {
    case FS_BOOLEAN:
      ob_type = ObTinyIntType;
      break;
    case FS_BIGINT:
      ob_type = ObIntType;
      break;
    case FS_DOUBLE:
      ob_type = ObDoubleType;
      break;
    case FS_DATE:
      ob_type = ObDateType;
      break;
    case FS_DATETIME:
      ob_type = ObDateTimeType;
      break;
    case FS_VARCHAR:
    case FS_NULL:
    default:
      ob_type = ObVarcharType;
      break;
  }
  meta.set_type(ob_type);
  if (ObVarcharType == ob_type) {
    meta.set_collation_level(CS_LEVEL_IMPLICIT);
    meta.set_collation_type(CS_TYPE_UTF8MB4_GENERAL_CI);
  }
  accuracy = ObAccuracy::DDL_DEFAULT_ACCURACY[ob_type];
}

int fs_scalar_to_obj(FSValueType col_type,
                     const char *ptr,
                     const int64_t len,
                     const bool quoted,
                     ObObj &obj)
{
  int ret = OB_SUCCESS;
  obj.reset();
  const bool is_null = (len <= 0 && !quoted);
  if (is_null) {
    obj.set_null();
  } else {
    switch (col_type) {
      case FS_BIGINT: {
        // the value passed classification during inference, safe to convert
        char tmp[32];
        int64_t n = len < static_cast<int64_t>(sizeof(tmp)) ? len : static_cast<int64_t>(sizeof(tmp)) - 1;
        MEMCPY(tmp, ptr, n);
        tmp[n] = '\0';
        obj.set_int(FS_STRTOLL(tmp, NULL, 10));
        break;
      }
      case FS_DOUBLE: {
        char tmp[64];
        int64_t n = len < static_cast<int64_t>(sizeof(tmp)) ? len : static_cast<int64_t>(sizeof(tmp)) - 1;
        MEMCPY(tmp, ptr, n);
        tmp[n] = '\0';
        obj.set_double(FS_STRTOD(tmp, NULL));
        break;
      }
      case FS_BOOLEAN: {
        // only "true"/"false" classify as boolean
        obj.set_tinyint((4 == len && 0 == fs_strncasecmp_ascii(ptr, "true", 4)) ? 1 : 0);
        break;
      }
      case FS_DATE: {
        ObString date_str(static_cast<int32_t>(len), ptr);
        int32_t date_val = 0;
        if (OB_FAIL(ObTimeConverter::str_to_date(date_str, date_val))) {
          LOG_WARN("fail to convert file date value", K(ret));
        } else {
          obj.set_date(date_val);
        }
        break;
      }
      case FS_DATETIME: {
        ObString dt_str(static_cast<int32_t>(len), ptr);
        ObTimeConvertCtx cvrt_ctx(NULL, false /* not timestamp */);
        int64_t dt_val = 0;
        if (OB_FAIL(ObTimeConverter::str_to_datetime(dt_str, cvrt_ctx, dt_val))) {
          LOG_WARN("fail to convert file datetime value", K(ret));
        } else {
          obj.set_datetime(dt_val);
        }
        break;
      }
      case FS_VARCHAR:
      case FS_NULL:
      default: {
        obj.set_varchar(ObString(static_cast<int32_t>(len), ptr));
        obj.set_collation_level(CS_LEVEL_IMPLICIT);
        obj.set_collation_type(CS_TYPE_UTF8MB4_GENERAL_CI);
        break;
      }
    }
  }
  return ret;
}

namespace {

struct FdReaderCtx
{
  static const int64_t READ_BUF_SIZE = 256 * 1024;
  int fd_;
  int64_t offset_;  // logical file offset of next read
  char *buf_;       // heap buffer, never thread-local (see fd_read_fn)
  FdReaderCtx() : fd_(-1), offset_(0), buf_(NULL) {}
  ~FdReaderCtx()
  {
    if (NULL != buf_) {
      free(buf_);
      buf_ = NULL;
    }
  }
};

int64_t fd_read_fn(void *ctx, const char **buf)
{
  FdReaderCtx *fc = static_cast<FdReaderCtx *>(ctx);
  // NOTE: the read buffer must NOT be thread-local storage. A thread_local
  // buffer of this size becomes part of the process static TLS block, which
  // makes every thread stack smaller than TLS + 4KB rejected by glibc
  // pthread_create with EINVAL. Keep it on the heap instead.
  static char empty_buf[1] = {0};
  int64_t n = 0;
  if (NULL == fc->buf_) {
    fc->buf_ = static_cast<char *>(malloc(static_cast<size_t>(FdReaderCtx::READ_BUF_SIZE)));
  }
  if (NULL == fc->buf_) {
    *buf = empty_buf;
  } else {
    n = pread(fc->fd_, fc->buf_, static_cast<size_t>(FdReaderCtx::READ_BUF_SIZE), fc->offset_);
    if (n > 0) {
      fc->offset_ += n;
      *buf = fc->buf_;
    } else {
      n = 0;
      *buf = empty_buf;
    }
  }
  return n;
}

class FdGuard
{
public:
  FdGuard() : fd_(-1) {}
  ~FdGuard() { if (fd_ >= 0) { close(fd_); } }
  int &fd() { return fd_; }
private:
  int fd_;
};

// inference state for a set of columns
struct InferCtx
{
  FSArena arena_;
  ObIArray<ObFileTableCol> *cols_;
  ObIAllocator *alloc_;

  InferCtx() : cols_(NULL), alloc_(NULL) {}

  int ensure_col(const char *name, int32_t name_len, int64_t &idx)
  {
    int ret = OB_SUCCESS;
    idx = -1;
    for (int64_t i = 0; i < cols_->count(); ++i) {
      ObFileTableCol &c = cols_->at(i);
      if (c.name_.length() == name_len
          && 0 == fs_strncasecmp_ascii(c.name_.ptr(), name, name_len)) {
        idx = i;
        break;
      }
    }
    if (-1 == idx) {
      ObFileTableCol col;
      col.has_null_ = true;  // an unseen column starts NULL until first value
      if (OB_FAIL(ob_write_string(*alloc_, ObString(name_len, name), col.name_))) {
        LOG_WARN("fail to copy column name", K(ret));
      } else if (OB_FAIL(cols_->push_back(col))) {
        LOG_WARN("fail to push column", K(ret));
      } else {
        idx = cols_->count() - 1;
      }
    }
    return ret;
  }

  int feed_type(const int64_t idx, FSValueType v)
  {
    int ret = OB_SUCCESS;
    ObFileTableCol &c = cols_->at(idx);
    if (c.meta_.get_type() == ObMaxType) {
      // first non-null value
      c.has_null_ = false;
      fs_type_to_ob_meta(v, c.meta_, c.accuracy_);
    } else {
      FSValueType cur = ob_meta_to_fs_type(c.meta_);
      FSValueType widened = fs_widen(cur, v);
      if (widened != cur) {
        fs_type_to_ob_meta(widened, c.meta_, c.accuracy_);
      }
    }
    return ret;
  }

  int feed(const int64_t idx, const char *ptr, int64_t len, bool quoted)
  {
    int ret = OB_SUCCESS;
    ObFileTableCol &c = cols_->at(idx);
    bool out_bool = false;
    int64_t out_i = 0;
    double out_d = 0;
    FSValueType v = fs_classify_scalar(ptr, static_cast<int32_t>(len), out_bool, out_i, out_d);
    if (FS_NULL == v) {
      c.has_null_ = true;
    } else {
      OZ (feed_type(idx, v));
    }
    UNUSED(quoted);
    return ret;
  }

  static FSValueType ob_meta_to_fs_type(const ObObjMeta &meta)
  {
    switch (meta.get_type()) {
      case ObTinyIntType: return FS_BOOLEAN;   // only boolean columns map to tinyint
      case ObIntType: return FS_BIGINT;
      case ObDoubleType: return FS_DOUBLE;
      case ObDateType: return FS_DATE;
      case ObDateTimeType: return FS_DATETIME;
      default: return FS_VARCHAR;
    }
  }
};

int infer_csv(const ObFileTableParam &param, InferCtx &ictx, int64_t &row_count)
{
  int ret = OB_SUCCESS;
  FdGuard fg;
  fg.fd() = open(param.path_.ptr(), O_RDONLY);
  if (fg.fd() < 0) {
    ret = OB_FILE_NOT_OPENED;
    LOG_WARN("fail to open file for inference", K(param.path_), K(errno));
  } else {
    FdReaderCtx rc;
    rc.fd_ = fg.fd();
    CsvScanner scanner;
    if (OB_FAIL(scanner.init(fd_read_fn, &rc,
                             param.delimiter_, param.quote_, param.escape_))) {
      LOG_WARN("fail to init csv scanner", K(ret));
    } else {
      OZ (scanner.skip_bom());
      static const int32_t MAX_COLS = 1024;
      CSVField fields[MAX_COLS];
      int32_t nfield = 0;
      int64_t rec_idx = 0;
      bool done = false;
      while (OB_SUCC(ret) && !done) {
        nfield = 0;
        int sret = scanner.next_record(fields, MAX_COLS, nfield);
        if (FS_ERR_EOF == sret) {
          done = true;
        } else if (FS_OK != sret) {
          ret = OB_INVALID_DATA;
          LOG_WARN("csv scan error during inference", K(sret), K(param.path_));
        } else if (0 == rec_idx && param.header_) {
          // header row: create columns in order (duplicates get __2 suffix)
          for (int32_t i = 0; OB_SUCC(ret) && i < nfield; ++i) {
            ObString raw(fields[i].len, fields[i].ptr);
            // check duplicate
            int64_t dup = 0;
            for (int64_t j = 0; j < ictx.cols_->count(); ++j) {
              if (0 == ictx.cols_->at(j).name_.case_compare(raw)) {
                ++dup;
              }
            }
            char name_buf[256];
            ObString col_name;
            if (dup > 0) {
              int32_t n = snprintf(name_buf, sizeof(name_buf), "%.*s__%ld",
                                   fields[i].len, fields[i].ptr, dup + 1);
              col_name.assign_ptr(name_buf, n);
            } else {
              col_name = raw;
            }
            ObFileTableCol col;
            col.has_null_ = false;
            if (OB_FAIL(ob_write_string(*ictx.alloc_, col_name, col.name_))) {
              LOG_WARN("fail to copy header name", K(ret));
            } else if (OB_FAIL(ictx.cols_->push_back(col))) {
              LOG_WARN("fail to push csv column", K(ret));
            }
          }
        } else {
          if (nfield > ictx.cols_->count() && !param.header_) {
            // no header: synthesize column names c1..cn
            for (int64_t i = ictx.cols_->count(); OB_SUCC(ret) && i < nfield; ++i) {
              char name_buf[32];
              int32_t n = snprintf(name_buf, sizeof(name_buf), "c%ld", i + 1);
              ObFileTableCol col;
              col.has_null_ = true;
              ObString nb(n, name_buf);
              if (OB_FAIL(ob_write_string(*ictx.alloc_, nb, col.name_))) {
                LOG_WARN("fail to copy synth name", K(ret));
              } else if (OB_FAIL(ictx.cols_->push_back(col))) {
                LOG_WARN("fail to push synth column", K(ret));
              }
            }
          }
          for (int32_t i = 0; OB_SUCC(ret) && i < nfield; ++i) {
            int64_t idx = i;
            if (i >= ictx.cols_->count()) {
              // row wider than header: extra fields ignored for schema
              break;
            }
            OZ (ictx.feed(idx, fields[i].ptr, fields[i].len, fields[i].quoted));
          }
          // missing trailing fields count as NULL
          for (int64_t i = nfield; OB_SUCC(ret) && i < ictx.cols_->count(); ++i) {
            ObFileTableCol &c = ictx.cols_->at(i);
            if (c.meta_.get_type() != ObMaxType) {
              c.has_null_ = true;
            }
          }
          ++row_count;
        }
        ++rec_idx;
      }
    }
  }
  return ret;
}

int infer_jsonl(const ObFileTableParam &param, InferCtx &ictx, int64_t &row_count)
{
  int ret = OB_SUCCESS;
  FdGuard fg;
  fg.fd() = open(param.path_.ptr(), O_RDONLY);
  if (fg.fd() < 0) {
    ret = OB_FILE_NOT_OPENED;
    LOG_WARN("fail to open jsonl file", K(param.path_), K(errno));
  } else {
    struct stat st;
    if (0 != fstat(fg.fd(), &st)) {
      ret = OB_FILE_NOT_OPENED;
      LOG_WARN("fstat failed", K(errno));
    } else {
      const int64_t file_len = st.st_size;
      char *data = static_cast<char *>(ictx.arena_.alloc(file_len + 1));
      if (OB_ISNULL(data)) {
        ret = OB_ALLOCATE_MEMORY_FAILED;
        LOG_WARN("fail to alloc for jsonl", K(file_len));
      } else if (file_len > 0) {
        int64_t off = 0;
        while (off < file_len) {
          int64_t n = pread(fg.fd(), data + off, file_len - off, off);
          if (n <= 0) {
            ret = OB_IO_ERROR;
            LOG_WARN("pread failed", K(ret), K(errno));
            break;
          }
          off += n;
        }
      }
      if (OB_FAIL(ret)) {
        // nothing
      } else {
        data[file_len] = '\0';
        int64_t line_start = 0;
        for (int64_t pos = 0; OB_SUCC(ret) && pos <= file_len; ++pos) {
          if (pos == file_len || '\n' == data[pos]) {
            int64_t line_len = pos - line_start;
            // trim \r and blanks
            while (line_len > 0 && ('\r' == data[line_start + line_len - 1])) {
              --line_len;
            }
            int64_t ls = line_start;
            while (line_len > 0 && (' ' == data[ls] || '\t' == data[ls])) { ++ls; --line_len; }
            while (line_len > 0 && (' ' == data[ls + line_len - 1] || '\t' == data[ls + line_len - 1])) { --line_len; }
            if (line_len > 0) {
              ObString line(static_cast<int32_t>(line_len), data + ls);
              ObJsonNode *node = NULL;
              const int64_t cols_before = ictx.cols_->count();
              if (OB_FAIL(ObJsonParser::get_tree(ictx.alloc_, line, node))) {
                ret = OB_INVALID_DATA;
                LOG_WARN("fail to parse json line", K(ret));
              } else if (OB_ISNULL(node) || ObJsonNodeType::J_OBJECT != node->json_type()) {
                ret = OB_INVALID_DATA;
                LOG_WARN("jsonl line is not an object");
              } else {
                ObJsonObject *obj = static_cast<ObJsonObject *>(node);
                const uint64_t cnt = obj->element_count();
                for (uint64_t k = 0; OB_SUCC(ret) && k < cnt; ++k) {
                  ObString key;
                  ObJsonNode *val = obj->get_value(k);
                  if (OB_FAIL(obj->get_key(k, key))) {
                    LOG_WARN("fail to get json key", K(ret));
                  } else {
                    int64_t idx = 0;
                    OZ (ictx.ensure_col(key.ptr(), static_cast<int32_t>(key.length()), idx));
                    if (OB_SUCC(ret)) {
                      if (OB_ISNULL(val) || ObJsonNodeType::J_NULL == val->json_type()) {
                        if (idx < cols_before) {
                          ictx.cols_->at(idx).has_null_ = true;
                        }
                      } else {
                        switch (val->json_type()) {
                          case ObJsonNodeType::J_BOOLEAN: {
                            OZ (ictx.feed_type(idx, FS_BOOLEAN));
                            break;
                          }
                          case ObJsonNodeType::J_INT: {
                            // a JSON number is BIGINT or DOUBLE by its json type,
                            // never re-classified from its textual form
                            int64_t v = static_cast<ObJsonInt *>(val)->value();
                            OZ (ictx.feed_type(idx, FS_BIGINT));
                            UNUSED(v);
                            break;
                          }
                          case ObJsonNodeType::J_UINT: {
                            uint64_t v = static_cast<ObJsonUint *>(val)->value();
                            if (v > static_cast<uint64_t>(INT64_MAX)) {
                              OZ (ictx.feed_type(idx, FS_DOUBLE));
                            } else {
                              OZ (ictx.feed_type(idx, FS_BIGINT));
                            }
                            break;
                          }
                          case ObJsonNodeType::J_DOUBLE:
                          case ObJsonNodeType::J_DECIMAL: {
                            OZ (ictx.feed_type(idx, FS_DOUBLE));
                            break;
                          }
                          case ObJsonNodeType::J_STRING: {
                            // a JSON string is always VARCHAR (DuckDB semantics)
                            OZ (ictx.feed_type(idx, FS_VARCHAR));
                            break;
                          }
                          default: {
                            // nested json: keep as varchar
                            OZ (ictx.feed_type(idx, FS_VARCHAR));
                            break;
                          }
                        }
                      }
                    }
                  }
                }
                // keys absent in this row count as NULL
                for (int64_t i = 0; OB_SUCC(ret) && i < cols_before; ++i) {
                  ObFileTableCol &c = ictx.cols_->at(i);
                  if (c.meta_.get_type() != ObMaxType) {
                    // mark if this key did not appear in the current row
                    bool found = false;
                    for (uint64_t k = 0; k < cnt; ++k) {
                      ObString key;
                      if (OB_SUCCESS == obj->get_key(k, key)
                          && key.case_compare(c.name_) == 0) {
                        found = true;
                        break;
                      }
                    }
                    if (!found) {
                      c.has_null_ = true;
                    }
                  }
                }
                ++row_count;
              }
            }
            line_start = pos + 1;
          }
        }
      }
    }
  }
  return ret;
}

int infer_parquet(const ObFileTableParam &param, InferCtx &ictx, int64_t &row_count)
{
  int ret = OB_SUCCESS;
  FdGuard fg;
  fg.fd() = open(param.path_.ptr(), O_RDONLY);
  if (fg.fd() < 0) {
    ret = OB_FILE_NOT_OPENED;
    LOG_WARN("fail to open parquet file", K(param.path_), K(errno));
  } else {
    struct stat st;
    if (0 != fstat(fg.fd(), &st)) {
      ret = OB_FILE_NOT_OPENED;
      LOG_WARN("fstat failed", K(errno));
    } else {
      const int64_t file_len = st.st_size;
      char *data = static_cast<char *>(ictx.arena_.alloc(file_len + 1));
      if (OB_ISNULL(data)) {
        ret = OB_ALLOCATE_MEMORY_FAILED;
        LOG_WARN("fail to alloc for parquet", K(file_len));
      } else {
        int64_t off = 0;
        while (off < file_len) {
          int64_t n = pread(fg.fd(), data + off, file_len - off, off);
          if (n <= 0) { ret = OB_IO_ERROR; break; }
          off += n;
        }
      }
      if (OB_FAIL(ret)) {
        // nothing
      } else if (file_len < 12 || 0 != MEMCMP(data + file_len - 4, "PAR1", 4)) {
        ret = OB_INVALID_DATA;
        LOG_WARN("not a parquet file", K(param.path_));
      } else {
        PQFileMeta meta;
        MEMSET(&meta, 0, sizeof(meta));
        if (OB_UNLIKELY(FS_OK != pq_parse_footer(data, file_len, ictx.arena_, meta))) {
          ret = OB_INVALID_DATA;
          LOG_WARN("fail to parse parquet footer", K(param.path_));
        } else {
          for (int32_t c = 0; OB_SUCC(ret) && c < meta.column_count; ++c) {
            PQColumn &pc = meta.columns[c];
            ObFileTableCol col;
            FSValueType vt = FS_VARCHAR;
            switch (pc.physical_type) {
              case PQ_BOOLEAN: vt = FS_BOOLEAN; break;
              case PQ_INT64:
                vt = (PQC_TIMESTAMP_MILLIS == pc.converted_type
                      || PQC_TIMESTAMP_MICROS == pc.converted_type) ? FS_DATETIME
                     : (PQC_DECIMAL == pc.converted_type && pc.scale > 0) ? FS_DOUBLE
                     : FS_BIGINT;
                break;
              case PQ_INT32:
                vt = (PQC_DATE == pc.converted_type) ? FS_DATE
                     : (PQC_TIMESTAMP_MILLIS == pc.converted_type
                        || PQC_TIMESTAMP_MICROS == pc.converted_type) ? FS_DATETIME
                     : (PQC_DECIMAL == pc.converted_type && pc.scale > 0) ? FS_DOUBLE
                     : FS_BIGINT;
                break;
              case PQ_INT96: vt = FS_DATETIME; break;
              case PQ_FLOAT:
              case PQ_DOUBLE: vt = FS_DOUBLE; break;
              default: vt = FS_VARCHAR; break;
            }
            fs_type_to_ob_meta(vt, col.meta_, col.accuracy_);
            col.has_null_ = (pc.max_def_level > 0);
            ObString name(pc.name_len, pc.name);
            if (OB_FAIL(ob_write_string(*ictx.alloc_, name, col.name_))) {
              LOG_WARN("fail to copy parquet col name", K(ret));
            } else if (OB_FAIL(ictx.cols_->push_back(col))) {
              LOG_WARN("fail to push parquet col", K(ret));
            }
          }
          row_count = meta.num_rows;
        }
      }
    }
  }
  return ret;
}

struct GlobEntry
{
  const char *name_;
  int32_t len_;
};

int glob_entry_cmp(const void *a, const void *b)
{
  const GlobEntry *ea = static_cast<const GlobEntry *>(a);
  const GlobEntry *eb = static_cast<const GlobEntry *>(b);
  int64_t n = ea->len_ < eb->len_ ? ea->len_ : eb->len_;
  int c = MEMCMP(ea->name_, eb->name_, n);
  if (0 == c) {
    c = ea->len_ - eb->len_;
  }
  return c;
}

int infer_glob(const ObFileTableParam &param, InferCtx &ictx, int64_t &row_count)
{
  int ret = OB_SUCCESS;
  ObFileTableCol col;
  col.has_null_ = false;
  fs_type_to_ob_meta(FS_VARCHAR, col.meta_, col.accuracy_);
  ObString colname("file");
  OZ (ob_write_string(*ictx.alloc_, colname, col.name_));
  OZ (ictx.cols_->push_back(col));
  // pattern = last path component; dir = prefix
  const char *p = param.path_.ptr();
  const int64_t plen = param.path_.length();
  int64_t dir_end = plen;
  for (int64_t i = plen - 1; i >= 0; --i) {
    if ('/' == p[i]) {
      dir_end = i + 1;
      break;
    }
  }
  // if the whole pattern has no wildcard, it is a single file
  bool has_wild = false;
  for (int64_t i = 0; i < plen; ++i) {
    if ('*' == p[i] || '?' == p[i] || '[' == p[i]) { has_wild = true; break; }
  }
  if (!has_wild) {
    row_count = 1;  // caller (op) lists the single file
  } else {
    ObString dir_str(static_cast<int32_t>(dir_end), p);
    char dir_buf[4096];
    if (dir_end >= static_cast<int64_t>(sizeof(dir_buf))) {
      ret = OB_INVALID_ARGUMENT;
      LOG_WARN("glob dir too long", K(dir_end));
    } else {
      MEMCPY(dir_buf, p, dir_end);
      dir_buf[dir_end] = '\0';
      DIR *dir = opendir(dir_buf);
      if (OB_ISNULL(dir)) {
        ret = OB_FILE_NOT_OPENED;
        LOG_WARN("fail to open dir for glob", K(dir_buf), K(errno));
      } else {
        // NOTE: entries are listed by the operator at execution time;
        // inference only validates that the directory exists and produces
        // the fixed 'file' column.
        struct dirent *ent = NULL;
        int64_t cnt = 0;
        while (NULL != (ent = readdir(dir))) {
          if ('.' == ent->d_name[0]) { continue; }
          ObString name(strlen(ent->d_name), ent->d_name);
          ObString pat(static_cast<int32_t>(plen - dir_end), p + dir_end);
          bool matched = false;
          if (FS_OK == fs_glob_match(pat.ptr(), pat.length(),
                                     name.ptr(), name.length(), matched)
              && matched) {
            ++cnt;
          }
        }
        closedir(dir);
        row_count = cnt;
      }
    }
  }
  return ret;
}

} // anonymous namespace

int infer_file_schema(const ObFileTableParam &param,
                      ObIAllocator &alloc,
                      ObIArray<ObFileTableCol> &cols)
{
  int ret = OB_SUCCESS;
  InferCtx ictx;
  ictx.cols_ = &cols;
  ictx.alloc_ = &alloc;
  if (OB_UNLIKELY(!param.is_valid())) {
    ret = OB_INVALID_ARGUMENT;
    LOG_WARN("invalid file table param", K(param.kind_));
  } else {
    int64_t row_count = 0;
    ictx.arena_.init(4 << 20);
    switch (param.kind_) {
      case FT_READ_CSV:
        OZ (infer_csv(param, ictx, row_count));
        break;
      case FT_READ_JSON:
      case FT_READ_NDJSON:
        OZ (infer_jsonl(param, ictx, row_count));
        break;
      case FT_READ_PARQUET:
        OZ (infer_parquet(param, ictx, row_count));
        break;
      case FT_GLOB:
        OZ (infer_glob(param, ictx, row_count));
        break;
      default:
        ret = OB_INVALID_ARGUMENT;
        break;
    }
    // every column needs a type; all-null columns become nullable varchar
    for (int64_t i = 0; OB_SUCC(ret) && i < cols.count(); ++i) {
      ObFileTableCol &c = cols.at(i);
      if (c.meta_.get_type() == ObMaxType) {
        c.has_null_ = true;
        fs_type_to_ob_meta(FS_VARCHAR, c.meta_, c.accuracy_);
      }
    }
  }
  return ret;
}

} // end namespace file_sql
} // end namespace oceanbase
