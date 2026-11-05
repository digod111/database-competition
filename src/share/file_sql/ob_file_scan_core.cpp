/**
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

#include "share/file_sql/ob_file_scan_core.h"
#include <stdio.h>
#include <stdlib.h>

namespace oceanbase
{
namespace file_sql
{

#define FS_TRY(expr) \
  do { \
    ret = (expr); \
    if (FS_OK != ret) { \
      return ret; \
    } \
  } while (0)

const char *fs_value_type_name(FSValueType type)
{
  const char *name = "UNKNOWN";
  switch (type) {
    case FS_NULL:
      name = "NULL";
      break;
    case FS_BOOLEAN:
      name = "BOOLEAN";
      break;
    case FS_BIGINT:
      name = "BIGINT";
      break;
    case FS_DOUBLE:
      name = "DOUBLE";
      break;
    case FS_VARCHAR:
      name = "VARCHAR";
      break;
    case FS_DATE:
      name = "DATE";
      break;
    case FS_DATETIME:
      name = "DATETIME";
      break;
    default:
      break;
  }
  return name;
}

// ---------------------------------------------------------------------------
// FSArena
// ---------------------------------------------------------------------------
int FSArena::init(int64_t cap)
{
  int ret = FS_OK;
  if (cap <= 0) {
    ret = FS_ERR_PARAM;
  } else if (NULL == (buf_ = static_cast<char *>(malloc(static_cast<size_t>(cap))))) {
    ret = FS_ERR_BUF_TOO_SMALL;
  } else {
    cap_ = cap;
    used_ = 0;
  }
  return ret;
}

void FSArena::reset()
{
  if (NULL != buf_) {
    free(buf_);
    buf_ = NULL;
  }
  cap_ = 0;
  used_ = 0;
}

char *FSArena::alloc(int64_t size)
{
  char *ret = NULL;
  if (size < 0 || used_ + size > cap_) {
    ret = NULL;
  } else {
    ret = buf_ + used_;
    used_ += size;
  }
  return ret;
}

char *FSArena::alloc_zero(int64_t size)
{
  char *ret = alloc(size);
  if (NULL != ret) {
    MEMSET(ret, 0, static_cast<size_t>(size));
  }
  return ret;
}

char *FSArena::strdup_range(const char *p, int64_t len)
{
  char *ret = NULL;
  if (NULL == p || len < 0) {
    ret = NULL;
  } else if (NULL != (ret = alloc(len + 1))) {
    MEMCPY(ret, p, static_cast<size_t>(len));
    ret[len] = '\0';
  }
  return ret;
}

// ---------------------------------------------------------------------------
// snappy raw format
// ---------------------------------------------------------------------------
static int snappy_read_varint(const char *&p, const char *end, uint64_t &v)
{
  int ret = FS_OK;
  int shift = 0;
  v = 0;
  bool done = false;
  while (p < end && !done) {
    uint8_t b = static_cast<uint8_t>(*p++);
    v |= static_cast<uint64_t>(b & 0x7F) << shift;
    if (0 == (b & 0x80)) {
      done = true;
    } else {
      shift += 7;
      if (shift > 63) {
        ret = FS_ERR_DATA;
      }
    }
  }
  if (FS_OK == ret && !done) {
    ret = FS_ERR_DATA;
  }
  return ret;
}

int fs_snappy_decompress(const char *in, int64_t in_len,
                         char *out, int64_t out_cap, int64_t &out_len)
{
  int ret = FS_OK;
  const char *p = in;
  const char *end = in + in_len;
  uint64_t expected = 0;
  int64_t pos = 0;
  out_len = 0;
  if (NULL == in || NULL == out || in_len <= 0) {
    ret = FS_ERR_PARAM;
  } else if (FS_OK != (ret = snappy_read_varint(p, end, expected))) {
    ret = FS_ERR_DATA;
  } else if (expected > static_cast<uint64_t>(out_cap)) {
    ret = FS_ERR_BUF_TOO_SMALL;
  } else {
    while (FS_OK == ret && p < end) {
      uint8_t tag = static_cast<uint8_t>(*p++);
      uint32_t type = tag & 0x03;
      if (0 == type) {
        uint64_t len = (tag >> 2) + 1;
        if (len > 60) {
          uint32_t extra_bytes = static_cast<uint32_t>(len - 60);
          if (p + extra_bytes > end) {
            ret = FS_ERR_DATA;
          } else {
            len = 0;
            for (uint32_t i = 0; i < extra_bytes; i++) {
              len |= static_cast<uint64_t>(static_cast<uint8_t>(*p++)) << (8 * i);
            }
            len += 1;
          }
        }
        if (FS_OK == ret) {
          if (p + len > end || pos + static_cast<int64_t>(len) > out_cap) {
            ret = FS_ERR_DATA;
          } else {
            MEMCPY(out + pos, p, static_cast<size_t>(len));
            pos += static_cast<int64_t>(len);
            p += len;
          }
        }
      } else {
        int64_t length = 0;
        int64_t offset = 0;
        if (1 == type) {
          if (p >= end) {
            ret = FS_ERR_DATA;
          } else {
            uint8_t b = static_cast<uint8_t>(*p++);
            length = 4 + ((tag >> 2) & 0x07);
            offset = (static_cast<int64_t>(tag >> 5) << 8) | b;
          }
        } else if (2 == type) {
          if (p + 2 > end) {
            ret = FS_ERR_DATA;
          } else {
            length = (tag >> 2) + 1;
            offset = static_cast<uint8_t>(p[0]) | (static_cast<uint8_t>(p[1]) << 8);
            p += 2;
          }
        } else {
          if (p + 4 > end) {
            ret = FS_ERR_DATA;
          } else {
            length = (tag >> 2) + 1;
            offset = static_cast<uint8_t>(p[0])
                     | (static_cast<uint8_t>(p[1]) << 8)
                     | (static_cast<uint8_t>(p[2]) << 16)
                     | (static_cast<int64_t>(static_cast<uint8_t>(p[3])) << 24);
            p += 4;
          }
        }
        if (FS_OK == ret) {
          if (offset <= 0 || offset > pos || pos + length > out_cap) {
            ret = FS_ERR_DATA;
          } else {
            for (int64_t i = 0; i < length; i++) {
              out[pos] = out[pos - offset];
              pos++;
            }
          }
        }
      }
    }
    if (FS_OK == ret) {
      if (pos != static_cast<int64_t>(expected)) {
        ret = FS_ERR_DATA;
      } else {
        out_len = pos;
      }
    }
  }
  return ret;
}

// ---------------------------------------------------------------------------
// thrift compact
// ---------------------------------------------------------------------------
enum ThriftCompactType
{
  TTC_STOP = 0,
  TTC_BOOL_TRUE = 1,
  TTC_BOOL_FALSE = 2,
  TTC_BYTE = 3,
  TTC_I16 = 4,
  TTC_I32 = 5,
  TTC_I64 = 6,
  TTC_DOUBLE = 7,
  TTC_BINARY = 8,
  TTC_LIST = 9,
  TTC_SET = 10,
  TTC_MAP = 11,
  TTC_STRUCT = 12,
};

FSThriftReader::FSThriftReader(const char *data, int64_t len)
    : p_(data), end_(data + len)
{}

int FSThriftReader::read_byte(uint8_t &b)
{
  int ret = FS_OK;
  if (p_ >= end_) {
    ret = FS_ERR_DATA;
  } else {
    b = static_cast<uint8_t>(*p_++);
  }
  return ret;
}

int FSThriftReader::read_varint(uint64_t &v)
{
  int ret = FS_OK;
  int shift = 0;
  v = 0;
  bool done = false;
  while (!done && FS_OK == ret) {
    if (p_ >= end_) {
      ret = FS_ERR_DATA;
    } else {
      uint8_t b = static_cast<uint8_t>(*p_++);
      v |= static_cast<uint64_t>(b & 0x7F) << shift;
      if (0 == (b & 0x80)) {
        done = true;
      } else {
        shift += 7;
        if (shift > 63) {
          ret = FS_ERR_DATA;
        }
      }
    }
  }
  return ret;
}

int FSThriftReader::read_zigzag(int64_t &v)
{
  int ret = FS_OK;
  uint64_t u = 0;
  if (FS_OK != (ret = read_varint(u))) {
    // nothing
  } else {
    v = static_cast<int64_t>(u >> 1) ^ (-static_cast<int64_t>(u & 1));
  }
  return ret;
}

int FSThriftReader::read_i16(int16_t &v)
{
  int64_t t = 0;
  int ret = read_zigzag(t);
  if (FS_OK == ret) {
    v = static_cast<int16_t>(t);
  }
  return ret;
}

int FSThriftReader::read_i32(int32_t &v)
{
  int64_t t = 0;
  int ret = read_zigzag(t);
  if (FS_OK == ret) {
    v = static_cast<int32_t>(t);
  }
  return ret;
}

int FSThriftReader::read_i64(int64_t &v)
{
  return read_zigzag(v);
}

int FSThriftReader::read_string(const char *&str, int32_t &len)
{
  int ret = FS_OK;
  uint64_t l = 0;
  if (FS_OK != (ret = read_varint(l))) {
    ret = FS_ERR_DATA;
  } else if (p_ + l > end_) {
    ret = FS_ERR_DATA;
  } else {
    len = static_cast<int32_t>(l);
    str = p_;
    p_ += l;
  }
  return ret;
}

int FSThriftReader::read_double(double &v)
{
  int ret = FS_OK;
  if (p_ + 8 > end_) {
    ret = FS_ERR_DATA;
  } else {
    MEMCPY(&v, p_, 8);
    p_ += 8;
  }
  return ret;
}

int FSThriftReader::read_field_begin(int16_t &last_fid, int16_t &fid, uint8_t &type)
{
  int ret = FS_OK;
  uint8_t b = 0;
  if (FS_OK != (ret = read_byte(b))) {
    // nothing
  } else if (0 == b) {
    type = TTC_STOP;
    fid = 0;
  } else {
    uint8_t delta = (b & 0xF0) >> 4;
    type = b & 0x0F;
    if (0 == delta) {
      ret = read_i16(fid);
      if (FS_OK == ret) {
        last_fid = fid;
      }
    } else {
      last_fid = static_cast<int16_t>(last_fid + delta);
      fid = last_fid;
    }
  }
  return ret;
}

int FSThriftReader::skip_value(uint8_t type)
{
  int ret = FS_OK;
  switch (type) {
    case TTC_BOOL_TRUE:
    case TTC_BOOL_FALSE:
      break;
    case TTC_BYTE: {
      uint8_t b = 0;
      ret = read_byte(b);
      break;
    }
    case TTC_I16:
    case TTC_I32:
    case TTC_I64: {
      int64_t v = 0;
      ret = read_zigzag(v);
      break;
    }
    case TTC_DOUBLE: {
      double d = 0;
      ret = read_double(d);
      break;
    }
    case TTC_BINARY: {
      const char *s = NULL;
      int32_t len = 0;
      ret = read_string(s, len);
      break;
    }
    case TTC_LIST:
    case TTC_SET: {
      uint8_t h = 0;
      if (FS_OK != (ret = read_byte(h))) {
        // nothing
      } else {
        int64_t count = (h >> 4) & 0x0F;
        uint8_t etype = h & 0x0F;
        if (15 == count) {
          int32_t c32 = 0;
          if (FS_OK != (ret = read_i32(c32))) {
            // nothing
          } else {
            count = c32;
          }
        }
        if (count < 0) {
          ret = FS_ERR_DATA;
        }
        for (int64_t i = 0; FS_OK == ret && i < count; i++) {
          ret = skip_value(etype);
        }
      }
      break;
    }
    case TTC_MAP: {
      uint8_t b = 0;
      if (FS_OK != (ret = read_byte(b))) {
        // nothing
      } else if (0 != b) {
        int32_t count = 0;
        uint8_t ktype = (b >> 4) & 0x0F;
        uint8_t vtype = b & 0x0F;
        if (FS_OK != (ret = read_i32(count))) {
          // nothing
        } else {
          for (int64_t i = 0; FS_OK == ret && i < count; i++) {
            if (FS_OK != (ret = skip_value(ktype))) {
            } else if (FS_OK != (ret = skip_value(vtype))) {
            }
          }
        }
      }
      break;
    }
    case TTC_STRUCT: {
      int16_t fid = 0;
      while (FS_OK == ret) {
        int16_t f = 0;
        uint8_t t = 0;
        if (FS_OK != (ret = read_field_begin(fid, f, t))) {
          break;
        } else if (TTC_STOP == t) {
          break;
        } else if (FS_OK != (ret = skip_value(t))) {
          break;
        }
      }
      break;
    }
    default:
      ret = FS_ERR_DATA;
      break;
  }
  return ret;
}

// ---------------------------------------------------------------------------
// RLE / bit-packed hybrid decoder
// ---------------------------------------------------------------------------
FSRleDecoder::FSRleDecoder()
    : data_(NULL),
      end_(NULL),
      bit_width_(0),
      in_run_(false),
      literal_run_(false),
      err_(false),
      rle_left_(0),
      literal_left_(0),
      rle_value_(0),
      run_start_(NULL),
      run_end_(NULL),
      run_bit_pos_(0)
{}

int FSRleDecoder::read_uleb128(uint64_t &v)
{
  int ret = FS_OK;
  int shift = 0;
  v = 0;
  bool done = false;
  while (!done && FS_OK == ret) {
    if (data_ >= end_) {
      ret = FS_ERR_DATA;
    } else {
      uint8_t b = *data_++;
      v |= static_cast<uint64_t>(b & 0x7F) << shift;
      if (0 == (b & 0x80)) {
        done = true;
      } else {
        shift += 7;
        if (shift > 63) {
          ret = FS_ERR_DATA;
        }
      }
    }
  }
  return ret;
}

int FSRleDecoder::load_next_run()
{
  int ret = FS_OK;
  uint64_t header = 0;
  if (FS_OK != (ret = read_uleb128(header))) {
    // nothing
  } else {
    int64_t run_count = header >> 1;
    literal_run_ = (header & 1) != 0;
    in_run_ = true;
    if (!literal_run_) {
      int32_t nbytes = (bit_width_ + 7) / 8;
      if (data_ + nbytes > end_) {
        ret = FS_ERR_DATA;
      } else {
        uint32_t v = 0;
        for (int32_t k = 0; k < nbytes; k++) {
          v |= static_cast<uint32_t>(data_[k]) << (8 * k);
        }
        data_ += nbytes;
        rle_value_ = v;
        rle_left_ = run_count;
        literal_left_ = 0;
      }
    } else {
      // bit-packed literal run: the header stores the number of GROUPS of
      // 8 values (parquet spec: "we always bit-pack a multiple of 8 values
      // at a time, so we only store the number of values / 8"); the real
      // value count is groups * 8 (padding values may trail)
      int64_t groups = run_count;
      int64_t real_count = groups * 8;
      int64_t run_bits = real_count * bit_width_;
      int64_t run_bytes = (run_bits + 7) / 8;
      if (data_ + run_bytes > end_) {
        ret = FS_ERR_DATA;
      } else {
        run_start_ = data_;
        run_bit_pos_ = 0;
        literal_left_ = real_count;
        rle_left_ = 0;
        run_end_ = data_ + run_bytes;
      }
    }
  }
  return ret;
}

int FSRleDecoder::init(const char *data, int64_t len, int32_t bit_width)
{
  int ret = FS_OK;
  data_ = reinterpret_cast<const uint8_t *>(data);
  end_ = data_ + len;
  bit_width_ = bit_width;
  in_run_ = false;
  literal_run_ = false;
  rle_left_ = 0;
  literal_left_ = 0;
  run_start_ = NULL;
  run_end_ = NULL;
  run_bit_pos_ = 0;
  err_ = false;
  if (bit_width_ < 0 || bit_width_ > 32) {
    ret = FS_ERR_PARAM;
    err_ = true;
  }
  // NOTE: the parquet RLE/bit-packed hybrid stream has NO leading total
  // count header; the value count comes from the page header. Decoding is
  // purely run-driven until the input is exhausted.
  return ret;
}

static uint32_t rle_read_bits(const uint8_t *buf, int64_t bit_off, int32_t bit_width)
{
  uint32_t v = 0;
  for (int32_t k = 0; k < bit_width; k++) {
    int64_t bit = bit_off + k;
    if ((buf[bit >> 3] >> (bit & 7)) & 1) {
      v |= (1u << k);
    }
  }
  return v;
}

int64_t FSRleDecoder::decode(int32_t *out, int64_t out_cap)
{
  int64_t decoded = 0;
  if (!err_ && NULL != out && out_cap > 0) {
    while (decoded < out_cap) {
      if (!in_run_) {
        if (data_ >= end_) {
          break; // clean end of stream
        }
        if (FS_OK != load_next_run()) {
          err_ = true;
          break;
        }
      }
      if (!literal_run_) {
        if (rle_left_ <= 0) {
          in_run_ = false;
          continue;
        }
        out[decoded++] = static_cast<int32_t>(rle_value_);
        rle_left_--;
      } else {
        if (literal_left_ <= 0) {
          in_run_ = false;
          data_ = run_end_;
          continue;
        }
        out[decoded++] = static_cast<int32_t>(
            rle_read_bits(run_start_, run_bit_pos_, bit_width_));
        run_bit_pos_ += bit_width_;
        literal_left_--;
        if (0 == literal_left_) {
          data_ = run_end_;
        }
      }
    }
  }
  return decoded;
}

// ---------------------------------------------------------------------------
// parquet
// ---------------------------------------------------------------------------
namespace pq_fields
{
  const int16_t SE_TYPE = 1;
  const int16_t SE_TYPE_LENGTH = 2;
  const int16_t SE_REPETITION = 3;
  const int16_t SE_NAME = 4;
  const int16_t SE_NUM_CHILDREN = 5;
  const int16_t SE_CONVERTED_TYPE = 6;
  const int16_t SE_SCALE = 7;
  const int16_t SE_PRECISION = 8;
  const int16_t CMD_TYPE = 1;
  const int16_t CMD_ENCODINGS = 2;
  const int16_t CMD_PATH_IN_SCHEMA = 3;
  const int16_t CMD_CODEC = 4;
  const int16_t CMD_NUM_VALUES = 5;
  const int16_t CMD_TOTAL_UNCOMPRESSED_SIZE = 6;
  const int16_t CMD_TOTAL_COMPRESSED_SIZE = 7;
  const int16_t CMD_DATA_PAGE_OFFSET = 9;
  const int16_t CMD_DICTIONARY_PAGE_OFFSET = 11;
  const int16_t RG_COLUMNS = 1;
  const int16_t RG_NUM_ROWS = 3;
  const int16_t FMD_VERSION = 1;
  const int16_t FMD_SCHEMA = 2;
  const int16_t FMD_NUM_ROWS = 3;
  const int16_t FMD_ROW_GROUPS = 4;
  const int16_t PH_TYPE = 1;
  const int16_t PH_UNCOMPRESSED_SIZE = 2;
  const int16_t PH_COMPRESSED_SIZE = 3;
  const int16_t PH_DATA_PAGE_HEADER = 5;
  const int16_t PH_DICT_PAGE_HEADER = 7;
  const int16_t PH_DATA_PAGE_HEADER_V2 = 8;
  const int16_t DPH_NUM_VALUES = 1;
  const int16_t DPH_ENCODING = 2;
  const int16_t DPH_DEF_LEVEL_ENC = 3;
  const int16_t DPH_REP_LEVEL_ENC = 4;
  const int16_t DICT_NUM_VALUES = 1;
  const int16_t DPH2_NUM_VALUES = 1;
  const int16_t DPH2_NUM_NULLS = 2;
  const int16_t DPH2_NUM_ROWS = 3;
  const int16_t DPH2_ENCODING = 4;
  const int16_t DPH2_DEF_LEVELS_LEN = 5;
  const int16_t DPH2_REP_LEVELS_LEN = 6;
  const int16_t DPH2_IS_COMPRESSED = 7;
}

enum PageType
{
  PAGE_DATA = 0,
  PAGE_INDEX = 1,
  PAGE_DICTIONARY = 2,
  PAGE_DATA_V2 = 3,
};

static int pq_parse_schema_element(FSThriftReader &reader,
                                   int32_t &ptype,
                                   int32_t &rep,
                                   const char *&name,
                                   int32_t &name_len,
                                   int32_t &converted,
                                   int32_t &scale,
                                   int32_t &precision,
                                   int32_t &type_length)
{
  int ret = FS_OK;
  int16_t sfid = 0;
  ptype = -1;
  rep = 0;
  name = NULL;
  name_len = 0;
  converted = PQC_NONE;
  scale = 0;
  precision = 0;
  type_length = 0;
  while (FS_OK == ret) {
    int16_t sf = 0;
    uint8_t st = 0;
    if (FS_OK != (ret = reader.read_field_begin(sfid, sf, st))) {
      break;
    } else if (TTC_STOP == st) {
      break;
    } else if (sf == pq_fields::SE_TYPE) {
      ret = reader.read_i32(ptype);
    } else if (sf == pq_fields::SE_TYPE_LENGTH) {
      ret = reader.read_i32(type_length);
    } else if (sf == pq_fields::SE_REPETITION) {
      ret = reader.read_i32(rep);
    } else if (sf == pq_fields::SE_NAME) {
      ret = reader.read_string(name, name_len);
    } else if (sf == pq_fields::SE_NUM_CHILDREN) {
      int32_t children = 0;
      ret = reader.read_i32(children);
    } else if (sf == pq_fields::SE_CONVERTED_TYPE) {
      ret = reader.read_i32(converted);
    } else if (sf == pq_fields::SE_SCALE) {
      ret = reader.read_i32(scale);
    } else if (sf == pq_fields::SE_PRECISION) {
      ret = reader.read_i32(precision);
    } else {
      ret = reader.skip_value(st);
    }
  }
  return ret;
}

int pq_parse_footer(const char *data, int64_t len, FSArena &arena, PQFileMeta &meta)
{
  int ret = FS_OK;
  MEMSET(&meta, 0, sizeof(meta));
  if (NULL == data || len < 12) {
    ret = FS_ERR_DATA;
  } else if (0 != MEMCMP(data, "PAR1", 4) || 0 != MEMCMP(data + len - 4, "PAR1", 4)) {
    ret = FS_ERR_DATA;
  } else {
    int32_t meta_len = 0;
    MEMCPY(&meta_len, data + len - 8, 4);
    if (meta_len <= 0 || len - 8 - meta_len < 4) {
      ret = FS_ERR_DATA;
    } else {
      const char *meta_start = data + len - 8 - meta_len;
      FSThriftReader reader(meta_start, meta_len);
      int16_t fid = 0;
      // ------- schema pass -------
      while (FS_OK == ret) {
        int16_t f = 0;
        uint8_t t = 0;
        if (FS_OK != (ret = reader.read_field_begin(fid, f, t))) {
          break;
        } else if (TTC_STOP == t) {
          break;
        } else if (f == pq_fields::FMD_NUM_ROWS) {
          ret = reader.read_i64(meta.num_rows);
        } else if (f == pq_fields::FMD_SCHEMA) {
          uint8_t h = 0;
          if (FS_OK != (ret = reader.read_byte(h))) {
            // nothing
          } else {
            int64_t count = (h >> 4) & 0x0F;
            if (15 == count) {
              int32_t c32 = 0;
              ret = reader.read_i32(c32);
              count = c32;
            }
            if (FS_OK == ret) {
              if (count <= 0 || count > 10000) {
                ret = FS_ERR_DATA;
              } else if (NULL == (meta.columns = reinterpret_cast<PQColumn *>(arena.alloc_zero(sizeof(PQColumn) * (count + 1))))) {
                ret = FS_ERR_BUF_TOO_SMALL;
              } else {
                int32_t leaf_count = 0;
                for (int64_t i = 0; FS_OK == ret && i < count; i++) {
                  int32_t ptype = -1;
                  int32_t rep = 0;
                  const char *name = NULL;
                  int32_t name_len = 0;
                  int32_t converted = PQC_NONE;
                  int32_t scale = 0;
                  int32_t precision = 0;
                  int32_t type_length = 0;
                  FS_TRY(pq_parse_schema_element(reader, ptype, rep, name,
                                                 name_len, converted, scale,
                                                 precision, type_length));
                  if (ptype >= 0) {
                    if (leaf_count >= count) {
                      ret = FS_ERR_DATA;
                      break;
                    } else {
                      PQColumn &col = meta.columns[leaf_count++];
                      col.name = name;
                      col.name_len = name_len;
                      col.physical_type = ptype;
                      col.converted_type = converted;
                      col.scale = scale;
                      col.precision = precision;
                      col.type_length = type_length;
                      col.max_rep_level = (2 == rep) ? 1 : 0;
                      col.max_def_level = (rep >= 1) ? 1 : 0;
                    }
                  }
                }
                if (FS_OK == ret) {
                  meta.column_count = leaf_count;
                }
              }
            }
          }
        } else {
          ret = reader.skip_value(t);
        }
      }
      // ------- row group pass (fresh reader) -------
      if (FS_OK == ret) {
        FSThriftReader reader2(meta_start, meta_len);
        int16_t fid2 = 0;
        int64_t base_row = 0;
        const int32_t MAX_CHUNKS_PER_RG = 4096;
        PQColumnChunk *tmp_chunks = reinterpret_cast<PQColumnChunk *>(arena.alloc_zero(sizeof(PQColumnChunk) * (MAX_CHUNKS_PER_RG + 1)));
        if (NULL == tmp_chunks) {
          ret = FS_ERR_BUF_TOO_SMALL;
        }
        while (FS_OK == ret) {
          int16_t f = 0;
          uint8_t t = 0;
          if (FS_OK != (ret = reader2.read_field_begin(fid2, f, t))) {
            break;
          } else if (TTC_STOP == t) {
            break;
          } else if (f == pq_fields::FMD_NUM_ROWS) {
            int64_t nr = 0;
            ret = reader2.read_i64(nr);
          } else if (f == pq_fields::FMD_SCHEMA) {
            uint8_t h = 0;
            if (FS_OK != (ret = reader2.read_byte(h))) {
              // nothing
            } else {
              int64_t count = (h >> 4) & 0x0F;
              if (15 == count) {
                int32_t c32 = 0;
                ret = reader2.read_i32(c32);
                count = c32;
              }
              for (int64_t i = 0; FS_OK == ret && i < count; i++) {
                int32_t ptype = -1;
                int32_t rep = 0;
                const char *name = NULL;
                int32_t name_len = 0;
                int32_t converted = PQC_NONE;
                int32_t scale = 0;
                int32_t precision = 0;
                int32_t type_length = 0;
                ret = pq_parse_schema_element(reader2, ptype, rep, name,
                                              name_len, converted, scale,
                                              precision, type_length);
              }
            }
          } else if (f == pq_fields::FMD_ROW_GROUPS) {
            uint8_t h = 0;
            if (FS_OK != (ret = reader2.read_byte(h))) {
              // nothing
            } else {
              int64_t count = (h >> 4) & 0x0F;
              if (15 == count) {
                int32_t c32 = 0;
                ret = reader2.read_i32(c32);
                count = c32;
              }
              if (FS_OK == ret) {
                if (count < 0 || count > 100000) {
                  ret = FS_ERR_DATA;
                } else if (NULL == (meta.row_groups = reinterpret_cast<PQRowGroup *>(arena.alloc_zero(sizeof(PQRowGroup) * (count + 1))))) {
                  ret = FS_ERR_BUF_TOO_SMALL;
                } else {
                  meta.row_group_count = static_cast<int32_t>(count);
                  for (int64_t i = 0; FS_OK == ret && i < count; i++) {
                    PQRowGroup &rg = meta.row_groups[i];
                    rg.base_row = base_row;
                    int32_t chunk_cnt = 0;
                    int16_t rf_fid = 0;
                    while (FS_OK == ret) {
                      int16_t rf = 0;
                      uint8_t rt = 0;
                      if (FS_OK != (ret = reader2.read_field_begin(rf_fid, rf, rt))) {
                        break;
                      } else if (TTC_STOP == rt) {
                        break;
                      } else if (rf == pq_fields::RG_NUM_ROWS) {
                        ret = reader2.read_i64(rg.num_rows);
                      } else if (rf == pq_fields::RG_COLUMNS) {
                        uint8_t ch = 0;
                        if (FS_OK != (ret = reader2.read_byte(ch))) {
                          // nothing
                        } else {
                          int64_t ccount = (ch >> 4) & 0x0F;
                          if (15 == ccount) {
                            int32_t c32 = 0;
                            ret = reader2.read_i32(c32);
                            ccount = c32;
                          }
                          if (FS_OK == ret) {
                            for (int64_t c = 0; FS_OK == ret && c < ccount; c++) {
                              // ColumnChunk struct
                              PQColumnChunk chunk;
                              MEMSET(&chunk, 0, sizeof(chunk));
                              chunk.leaf_index = -1;
                              int16_t cc_fid = 0;
                              while (FS_OK == ret) {
                                int16_t cf = 0;
                                uint8_t ct = 0;
                                if (FS_OK != (ret = reader2.read_field_begin(cc_fid, cf, ct))) {
                                  break;
                                } else if (TTC_STOP == ct) {
                                  break;
                                } else if (3 == cf) {
                                  // ColumnChunk.meta_data: ColumnMetaData
                                  int16_t md_fid = 0;
                                  while (FS_OK == ret) {
                                    int16_t mf = 0;
                                    uint8_t mt = 0;
                                    if (FS_OK != (ret = reader2.read_field_begin(md_fid, mf, mt))) {
                                      break;
                                    } else if (TTC_STOP == mt) {
                                      break;
                                    } else if (pq_fields::CMD_TYPE == mf) {
                                      int32_t pt = 0;
                                      ret = reader2.read_i32(pt);
                                      chunk.physical_type = pt;
                                    } else if (pq_fields::CMD_PATH_IN_SCHEMA == mf) {
                                      uint8_t ph = 0;
                                      if (FS_OK != (ret = reader2.read_byte(ph))) {
                                        // nothing
                                      } else {
                                        int64_t pcount = (ph >> 4) & 0x0F;
                                        if (15 == pcount) {
                                          int32_t c32 = 0;
                                          ret = reader2.read_i32(c32);
                                          pcount = c32;
                                        }
                                        for (int64_t pn = 0; FS_OK == ret && pn < pcount; pn++) {
                                          const char *pn_str = NULL;
                                          int32_t pn_len = 0;
                                          if (FS_OK != (ret = reader2.read_string(pn_str, pn_len))) {
                                            // nothing
                                          } else if (chunk.leaf_index < 0 && pn_len > 0) {
                                            for (int32_t li = 0; li < meta.column_count; li++) {
                                              if (meta.columns[li].name_len == pn_len
                                                  && 0 == MEMCMP(meta.columns[li].name, pn_str, pn_len)) {
                                                chunk.leaf_index = li;
                                                break;
                                              }
                                            }
                                          }
                                        }
                                      }
                                    } else if (pq_fields::CMD_CODEC == mf) {
                                      int32_t codec = 0;
                                      ret = reader2.read_i32(codec);
                                      chunk.codec = codec;
                                    } else if (pq_fields::CMD_NUM_VALUES == mf) {
                                      ret = reader2.read_i64(chunk.num_values);
                                    } else if (pq_fields::CMD_TOTAL_COMPRESSED_SIZE == mf) {
                                      ret = reader2.read_i64(chunk.total_compressed_size);
                                    } else if (pq_fields::CMD_DATA_PAGE_OFFSET == mf) {
                                      ret = reader2.read_i64(chunk.data_page_offset);
                                    } else if (pq_fields::CMD_DICTIONARY_PAGE_OFFSET == mf) {
                                      ret = reader2.read_i64(chunk.dict_page_offset);
                                    } else {
                                      ret = reader2.skip_value(mt);
                                    }
                                  }
                                } else {
                                  ret = reader2.skip_value(ct);
                                }
                              }
                              if (FS_OK == ret) {
                                if (chunk_cnt >= MAX_CHUNKS_PER_RG) {
                                  ret = FS_ERR_BUF_TOO_SMALL;
                                } else {
                                  tmp_chunks[chunk_cnt] = chunk;
                                  chunk_cnt++;
                                }
                              }
                            }
                          }
                        }
                      } else {
                        ret = reader2.skip_value(rt);
                      }
                    }
                    if (FS_OK == ret) {
                      if (NULL == (rg.chunks = reinterpret_cast<PQColumnChunk *>(arena.alloc_zero(sizeof(PQColumnChunk) * (chunk_cnt + 1))))) {
                        ret = FS_ERR_BUF_TOO_SMALL;
                      } else {
                        MEMCPY(rg.chunks, tmp_chunks, static_cast<size_t>(sizeof(PQColumnChunk) * chunk_cnt));
                        rg.chunk_count = chunk_cnt;
                      }
                      base_row += rg.num_rows;
                    }
                  }
                }
              }
            }
          } else {
            ret = reader2.skip_value(t);
          }
        }
      }
    }
  }
  return ret;
}

int pq_find_chunk(const PQRowGroup &rg, int32_t leaf_index, PQColumnChunk &out)
{
  int ret = FS_ERR_DATA;
  for (int32_t i = 0; i < rg.chunk_count; i++) {
    if (rg.chunks[i].leaf_index == leaf_index) {
      out = rg.chunks[i];
      ret = FS_OK;
      break;
    }
  }
  return ret;
}

struct FSPlainValue
{
  bool b;
  int64_t i;
  double d;
  const char *s;
  int32_t s_len;
};

// read one PLAIN value; bool_bit tracks the bit position for PLAIN booleans
// whose bitmap spans the whole values region
static int pq_plain_read_one(const char *&p, const char *end,
                             const PQColumn &col, int32_t &bool_bit,
                             FSPlainValue &v)
{
  int ret = FS_OK;
  v.b = false;
  v.i = 0;
  v.d = 0;
  v.s = NULL;
  v.s_len = 0;
  switch (col.physical_type) {
    case PQ_BOOLEAN: {
      // booleans are bit packed LSB-first; p marks the byte containing the
      // next unread bit (bool_bit is relative to p)
      if (p >= end) {
        ret = FS_ERR_DATA;
      } else {
        v.b = ((static_cast<const uint8_t *>(static_cast<const void *>(p))[bool_bit >> 3] >> (bool_bit & 7)) & 1) != 0;
        bool_bit++;
        if (8 == bool_bit) {
          bool_bit = 0;
          p++;
        }
      }
      break;
    }
    case PQ_INT32: {
      if (p + 4 > end) {
        ret = FS_ERR_DATA;
      } else {
        int32_t x = 0;
        MEMCPY(&x, p, 4);
        p += 4;
        v.i = x;
        if (PQC_DECIMAL == col.converted_type && col.scale > 0) {
          double dd = static_cast<double>(x);
          for (int32_t k = 0; k < col.scale; k++) {
            dd /= 10.0;
          }
          v.d = dd;
          v.i = x;
        }
      }
      break;
    }
    case PQ_INT64: {
      if (p + 8 > end) {
        ret = FS_ERR_DATA;
      } else {
        int64_t x = 0;
        MEMCPY(&x, p, 8);
        p += 8;
        v.i = x;
        if (PQC_DECIMAL == col.converted_type && col.scale > 0) {
          double dd = static_cast<double>(x);
          for (int32_t k = 0; k < col.scale; k++) {
            dd /= 10.0;
          }
          v.d = dd;
        }
      }
      break;
    }
    case PQ_INT96: {
      if (p + 12 > end) {
        ret = FS_ERR_DATA;
      } else {
        int64_t nanos = 0;
        int32_t julian = 0;
        MEMCPY(&nanos, p, 8);
        MEMCPY(&julian, p + 8, 4);
        p += 12;
        int64_t days = static_cast<int64_t>(julian) - 2440588;
        v.i = days * 86400000000LL + nanos / 1000; // micros since epoch
      }
      break;
    }
    case PQ_FLOAT: {
      if (p + 4 > end) {
        ret = FS_ERR_DATA;
      } else {
        float f = 0;
        MEMCPY(&f, p, 4);
        p += 4;
        v.d = f;
      }
      break;
    }
    case PQ_DOUBLE: {
      if (p + 8 > end) {
        ret = FS_ERR_DATA;
      } else {
        MEMCPY(&v.d, p, 8);
        p += 8;
      }
      break;
    }
    case PQ_BYTE_ARRAY: {
      if (p + 4 > end) {
        ret = FS_ERR_DATA;
      } else {
        int32_t l = 0;
        MEMCPY(&l, p, 4);
        p += 4;
        if (l < 0 || p + l > end) {
          ret = FS_ERR_DATA;
        } else {
          v.s = p;
          v.s_len = l;
          p += l;
        }
      }
      break;
    }
    case PQ_FIXED_LEN_BYTE_ARRAY: {
      int32_t l = col.type_length;
      if (l <= 0 || p + l > end) {
        ret = FS_ERR_DATA;
      } else {
        v.s = p;
        v.s_len = l;
        if (PQC_DECIMAL == col.converted_type && col.scale > 0 && l <= 9) {
          int64_t x = (p[0] & 0x80) ? -1 : 0;
          for (int32_t k = 0; k < l; k++) {
            x = (x << 8) | static_cast<uint8_t>(p[k]);
          }
          double dd = static_cast<double>(x);
          for (int32_t k = 0; k < col.scale; k++) {
            dd /= 10.0;
          }
          v.d = dd;
          v.i = x;
        }
        p += l;
      }
      break;
    }
    default:
      ret = FS_ERR_UNSUPPORTED;
      break;
  }
  return ret;
}

static int pq_compute_bit_width(int32_t max_level, int32_t &width)
{
  width = 0;
  while (max_level > 0) {
    width++;
    max_level >>= 1;
  }
  return FS_OK;
}

// store one decoded value into out at index idx
static int pq_store_value(FSColumnData &out, int64_t idx, const PQColumn &col,
                          const FSPlainValue &v)
{
  int ret = FS_OK;
  out.not_null[idx] = 1;
  switch (out.value_type) {
    case FS_BOOLEAN:
      out.bools[idx] = v.b ? 1 : 0;
      break;
    case FS_BIGINT:
      out.ints[idx] = v.i;
      break;
    case FS_DOUBLE:
      out.dbls[idx] = v.d;
      break;
    case FS_DATE:
    case FS_DATETIME:
      out.ints[idx] = v.i;
      break;
    case FS_VARCHAR:
      out.strs[idx].ptr = v.s;
      out.strs[idx].len = v.s_len;
      break;
    default:
      ret = FS_ERR_PARAM;
      break;
  }
  return ret;
}

int pq_read_column_chunk(const char *data, int64_t file_len,
                         const PQColumn &col, const PQColumnChunk &chunk,
                         FSArena &arena, FSColumnData &out)
{
  int ret = FS_OK;
  MEMSET(&out, 0, sizeof(out));
  switch (col.physical_type) {
    case PQ_BOOLEAN:
      out.value_type = FS_BOOLEAN;
      break;
    case PQ_INT32:
    case PQ_INT64:
      if (PQC_DATE == col.converted_type) {
        out.value_type = FS_DATE;
      } else if (PQC_TIMESTAMP_MILLIS == col.converted_type
                 || PQC_TIMESTAMP_MICROS == col.converted_type) {
        out.value_type = FS_DATETIME;
      } else if (PQC_DECIMAL == col.converted_type && col.scale > 0) {
        out.value_type = FS_DOUBLE;
      } else {
        out.value_type = FS_BIGINT;
      }
      break;
    case PQ_FLOAT:
    case PQ_DOUBLE:
      out.value_type = FS_DOUBLE;
      break;
    case PQ_BYTE_ARRAY:
      out.value_type = FS_VARCHAR;
      break;
    case PQ_FIXED_LEN_BYTE_ARRAY:
      out.value_type = (PQC_DECIMAL == col.converted_type && col.scale > 0) ? FS_DOUBLE : FS_VARCHAR;
      break;
    default:
      out.value_type = FS_VARCHAR;
      break;
  }
  int64_t count = chunk.num_values;
  if (count < 0 || count > (64LL << 20)) {
    ret = FS_ERR_DATA;
  } else if (NULL == (out.not_null = reinterpret_cast<uint8_t *>(arena.alloc_zero(count + 1)))
             || NULL == (out.bools = reinterpret_cast<uint8_t *>(arena.alloc_zero(count + 1)))
             || NULL == (out.ints = reinterpret_cast<int64_t *>(arena.alloc_zero(sizeof(int64_t) * (count + 1))))
             || NULL == (out.dbls = reinterpret_cast<double *>(arena.alloc_zero(sizeof(double) * (count + 1))))
             || NULL == (out.strs = reinterpret_cast<FSStr *>(arena.alloc_zero(sizeof(FSStr) * (count + 1))))) {
    ret = FS_ERR_BUF_TOO_SMALL;
  } else {
    out.count = count;
    int64_t first_page_off = chunk.data_page_offset;
    if (chunk.dict_page_offset > 0 && chunk.dict_page_offset < first_page_off) {
      first_page_off = chunk.dict_page_offset;
    }
    if (first_page_off < 0 || first_page_off >= file_len) {
      ret = FS_ERR_DATA;
    } else {
      const char *cp = data + first_page_off;
      const char *cend = data + file_len;
      if (chunk.total_compressed_size > 0 && cp + chunk.total_compressed_size < cend) {
        cend = cp + chunk.total_compressed_size;
      }
      FSPlainValue *dict = NULL;
      int64_t dict_count = 0;
      char *decomp_buf = NULL;
      int64_t decomp_cap = 0;
      int64_t produced = 0;
      int32_t plain_bool_bit = 0; // bit position inside V1 PLAIN boolean pages
      while (FS_OK == ret && produced < count) {
        // ---- page header ----
        FSThriftReader hr(cp, cend - cp);
        int32_t page_type = 0;
        int32_t uncompressed_size = 0;
        int32_t compressed_size = 0;
        int32_t dph_values = 0;
        int32_t dph_encoding = 0;
        int32_t dict_num_values = 0;
        int32_t v2_num_nulls = 0;
        int32_t v2_num_rows = 0;
        int32_t v2_def_len = 0;
        int32_t v2_rep_len = 0;
        bool v2_compressed = false;
        int16_t fid = 0;
        while (FS_OK == ret) {
          int16_t f = 0;
          uint8_t t = 0;
          if (FS_OK != (ret = hr.read_field_begin(fid, f, t))) {
            break;
          } else if (TTC_STOP == t) {
            break;
          } else if (f == pq_fields::PH_TYPE) {
            ret = hr.read_i32(page_type);
          } else if (f == pq_fields::PH_UNCOMPRESSED_SIZE) {
            ret = hr.read_i32(uncompressed_size);
          } else if (f == pq_fields::PH_COMPRESSED_SIZE) {
            ret = hr.read_i32(compressed_size);
          } else if (f == pq_fields::PH_DATA_PAGE_HEADER) {
            int16_t dfid = 0;
            while (FS_OK == ret) {
              int16_t df = 0;
              uint8_t dt = 0;
              if (FS_OK != (ret = hr.read_field_begin(dfid, df, dt))) {
                break;
              } else if (TTC_STOP == dt) {
                break;
              } else if (df == pq_fields::DPH_NUM_VALUES) {
                ret = hr.read_i32(dph_values);
              } else if (df == pq_fields::DPH_ENCODING) {
                ret = hr.read_i32(dph_encoding);
              } else if (df == pq_fields::DPH_DEF_LEVEL_ENC
                         || df == pq_fields::DPH_REP_LEVEL_ENC) {
                int32_t enc = 0;
                ret = hr.read_i32(enc);
              } else {
                ret = hr.skip_value(dt);
              }
            }
          } else if (f == pq_fields::PH_DICT_PAGE_HEADER) {
            int16_t dfid = 0;
            while (FS_OK == ret) {
              int16_t df = 0;
              uint8_t dt = 0;
              if (FS_OK != (ret = hr.read_field_begin(dfid, df, dt))) {
                break;
              } else if (TTC_STOP == dt) {
                break;
              } else if (df == pq_fields::DICT_NUM_VALUES) {
                ret = hr.read_i32(dict_num_values);
              } else {
                ret = hr.skip_value(dt);
              }
            }
          } else if (f == pq_fields::PH_DATA_PAGE_HEADER_V2) {
            int16_t dfid = 0;
            while (FS_OK == ret) {
              int16_t df = 0;
              uint8_t dt = 0;
              if (FS_OK != (ret = hr.read_field_begin(dfid, df, dt))) {
                break;
              } else if (TTC_STOP == dt) {
                break;
              } else if (df == pq_fields::DPH2_NUM_VALUES) {
                ret = hr.read_i32(dph_values);
              } else if (df == pq_fields::DPH2_NUM_NULLS) {
                ret = hr.read_i32(v2_num_nulls);
              } else if (df == pq_fields::DPH2_NUM_ROWS) {
                ret = hr.read_i32(v2_num_rows);
              } else if (df == pq_fields::DPH2_ENCODING) {
                ret = hr.read_i32(dph_encoding);
              } else if (df == pq_fields::DPH2_DEF_LEVELS_LEN) {
                ret = hr.read_i32(v2_def_len);
              } else if (df == pq_fields::DPH2_REP_LEVELS_LEN) {
                ret = hr.read_i32(v2_rep_len);
              } else if (df == pq_fields::DPH2_IS_COMPRESSED) {
                // compact protocol: bool value is carried in the field
                // header type itself (1 = true, 2 = false), no payload
                v2_compressed = (TTC_BOOL_TRUE == dt);
              } else {
                ret = hr.skip_value(dt);
              }
            }
          } else {
            ret = hr.skip_value(t);
          }
        }
        if (FS_OK != ret) {
          break;
        }
        const char *page_data = hr.p_;
        if (compressed_size < 0 || page_data + compressed_size > cend) {
          ret = FS_ERR_DATA;
          break;
        }
        cp = page_data + compressed_size;
        // ---- decompress ----
        const char *vstart = page_data;
        int64_t vlen = compressed_size;
        char *v2_levels = NULL;
        int64_t v2_levels_len = 0;
        if (PAGE_DATA == page_type || PAGE_DICTIONARY == page_type) {
          if (PQ_CODEC_SNAPPY == chunk.codec) {
            if (uncompressed_size <= 0) {
              ret = FS_ERR_DATA;
              break;
            } else {
              // allocate a FRESH buffer per page: string values may point
              // into previous pages' decompressed data (dictionary pages),
              // so reusing one buffer would corrupt them
              if (NULL == (decomp_buf = reinterpret_cast<char *>(arena.alloc(uncompressed_size + 16)))) {
                ret = FS_ERR_BUF_TOO_SMALL;
                break;
              } else {
                decomp_cap = uncompressed_size;
              }
            }
            int64_t dlen = 0;
            if (FS_OK != (ret = fs_snappy_decompress(page_data, compressed_size,
                                                     decomp_buf, decomp_cap, dlen))) {
              break;
            }
            vstart = decomp_buf;
            vlen = dlen;
          }
        } else if (PAGE_DATA_V2 == page_type) {
          v2_levels_len = v2_def_len + v2_rep_len;
          v2_levels = const_cast<char *>(page_data);
          const char *vcomp = page_data + v2_levels_len;
          int64_t vcomp_len = compressed_size - v2_levels_len;
          if (v2_compressed && PQ_CODEC_SNAPPY == chunk.codec) {
            if (uncompressed_size <= 0) {
              ret = FS_ERR_DATA;
              break;
            } else {
              // fresh buffer per page (see the V1 note above)
              if (NULL == (decomp_buf = reinterpret_cast<char *>(arena.alloc(uncompressed_size + 16)))) {
                ret = FS_ERR_BUF_TOO_SMALL;
                break;
              } else {
                decomp_cap = uncompressed_size;
              }
            }
            int64_t dlen = 0;
            if (FS_OK != (ret = fs_snappy_decompress(vcomp, vcomp_len,
                                                     decomp_buf, decomp_cap, dlen))) {
              break;
            }
            vstart = decomp_buf;
            vlen = dlen;
          } else {
            vstart = vcomp;
            vlen = vcomp_len;
          }
        } else {
          continue; // index page or unsupported: skip
        }
        const char *vend = vstart + vlen;
        // ---- page body ----
        if (PAGE_DICTIONARY == page_type) {
          if (dict_num_values > 0 && dict_num_values <= (1LL << 22)) {
            if (NULL == (dict = reinterpret_cast<FSPlainValue *>(arena.alloc_zero(sizeof(FSPlainValue) * (dict_num_values + 1))))) {
              ret = FS_ERR_BUF_TOO_SMALL;
            } else {
              dict_count = dict_num_values;
              const char *dp = vstart;
              int32_t dbit = 0;
              for (int64_t i = 0; FS_OK == ret && i < dict_num_values; i++) {
                ret = pq_plain_read_one(dp, vend, col, dbit, dict[i]);
              }
            }
          } else {
            dict_count = 0;
          }
        } else if (PAGE_DATA == page_type || PAGE_DATA_V2 == page_type) {
          bool is_v2 = (PAGE_DATA_V2 == page_type);
          int64_t page_values = dph_values;
          if (page_values <= 0 || page_values > (1LL << 32)) {
            ret = FS_ERR_DATA;
            break;
          }
          int32_t def_width = 0;
          pq_compute_bit_width(col.max_def_level, def_width);
          int32_t rep_width = 0;
          pq_compute_bit_width(col.max_rep_level, rep_width);
          if (rep_width > 0) {
            ret = FS_ERR_UNSUPPORTED;
            break;
          }
          const char *lp = vstart;
          const char *lvend = vend;
          if (!is_v2) {
            if (def_width > 0) {
              // V1: RLE-encoded levels preceded by a 4-byte length prefix
              if (lp + 4 > lvend) {
                ret = FS_ERR_DATA;
                break;
              } else {
                int32_t len = 0;
                MEMCPY(&len, lp, 4);
                lp += 4;
                if (len < 0 || lp + len > lvend) {
                  ret = FS_ERR_DATA;
                  break;
                }
                lvend = lp + len;
              }
            }
          } else {
            // V2: levels are uncompressed, sizes from the page header
            lp = v2_levels;
            lvend = v2_levels + v2_levels_len;
          }
          // decode definition levels
          int32_t *defs = NULL;
          if (def_width > 0) {
            if (NULL == (defs = reinterpret_cast<int32_t *>(arena.alloc(sizeof(int32_t) * (page_values + 1))))) {
              ret = FS_ERR_BUF_TOO_SMALL;
              break;
            } else {
              FSRleDecoder def_dec;
              if (FS_OK != (ret = def_dec.init(lp, lvend - lp, def_width))) {
                break;
              } else if (def_dec.decode(defs, page_values) != page_values) {
                ret = FS_ERR_DATA;
                break;
              }
            }
          }
          // values region: for V2 the values start at vstart (the levels
          // live in the uncompressed page head and vstart already points
          // past them, into the decompressed buffer when compressed);
          // for V1 values follow the RLE levels block when present
          const char *vp = vstart;
          if (!is_v2 && def_width > 0) {
            vp = lvend;
          }
          const char *vend2 = vend;
          if (2 == dph_encoding || 8 == dph_encoding) {
            // PLAIN_DICTIONARY / RLE_DICTIONARY
            if (NULL == dict || dict_count <= 0) {
              ret = FS_ERR_DATA;
              break;
            } else if (vp >= vend2) {
              ret = FS_ERR_DATA;
              break;
            } else {
              int32_t width = static_cast<uint8_t>(*vp++);
              FSRleDecoder idx_dec;
              bool use_rle = (width > 0);
              if (use_rle && FS_OK != (ret = idx_dec.init(vp, vend2 - vp, width))) {
                break;
              } else {
                while (FS_OK == ret && produced < count) {
                  // the values stream only holds indices for non-null rows
                  int32_t def = (NULL != defs) ? defs[produced] : col.max_def_level;
                  if (def >= col.max_def_level) {
                    int32_t idx = 0;
                    if (use_rle) {
                      if (idx_dec.decode(&idx, 1) != 1) {
                        ret = FS_ERR_DATA;
                        break;
                      }
                    }
                    if (idx < 0 || idx >= dict_count) {
                      ret = FS_ERR_DATA;
                      break;
                    } else if (FS_OK != (ret = pq_store_value(out, produced, col, dict[idx]))) {
                      break;
                    }
                  }
                  produced++;
                }
              }
            }
          } else if (0 == dph_encoding) {
            // PLAIN
            while (FS_OK == ret && produced < count) {
              int32_t def = (NULL != defs) ? defs[produced] : col.max_def_level;
              if (def >= col.max_def_level) {
                FSPlainValue v;
                if (FS_OK != (ret = pq_plain_read_one(vp, vend2, col, plain_bool_bit, v))) {
                  break;
                } else if (FS_OK != (ret = pq_store_value(out, produced, col, v))) {
                  break;
                }
              }
              produced++;
            }
          } else if (3 == dph_encoding && FS_BOOLEAN == out.value_type) {
            // RLE-encoded booleans: 4-byte length prefix then the hybrid
            // stream with an implicit bit width of 1 (no width byte)
            if (vp + 4 > vend2) {
              ret = FS_ERR_DATA;
              break;
            } else {
              int32_t rlen = 0;
              MEMCPY(&rlen, vp, 4);
              vp += 4;
              if (rlen < 0 || vp + rlen > vend2) {
                ret = FS_ERR_DATA;
                break;
              } else {
                FSRleDecoder bool_dec;
                if (FS_OK != (ret = bool_dec.init(vp, rlen, 1))) {
                  break;
                } else {
                  while (FS_OK == ret && produced < count) {
                    int32_t def = (NULL != defs) ? defs[produced] : col.max_def_level;
                    if (def >= col.max_def_level) {
                      int32_t bit = 0;
                      if (bool_dec.decode(&bit, 1) != 1) {
                        ret = FS_ERR_DATA;
                        break;
                      } else {
                        FSPlainValue v;
                        v.b = (bit != 0);
                        v.i = 0;
                        v.d = 0;
                        v.s = NULL;
                        v.s_len = 0;
                        if (FS_OK != (ret = pq_store_value(out, produced, col, v))) {
                          break;
                        }
                      }
                    }
                    produced++;
                  }
                }
              }
            }
          } else {
            ret = FS_ERR_UNSUPPORTED;
            break;
          }
          if (FS_OK == ret && produced < count && page_values + produced <= count) {
            // continue with the next page; boolean PLAIN bitmap resets per page
            plain_bool_bit = 0;
          }
        } else {
          // unknown page type: skip
        }
      }
      if (FS_OK == ret && produced < count) {
        ret = FS_ERR_DATA;
      }
    }
  }
  return ret;
}

// ---------------------------------------------------------------------------
// CSV streaming scanner
// ---------------------------------------------------------------------------
CsvScanner::CsvScanner()
    : read_fn_(NULL),
      read_ctx_(NULL),
      buf_(NULL),
      buf_cap_(0),
      buf_len_(0),
      buf_pos_(0),
      eof_(false),
      delimiter_(','),
      quote_('"'),
      escape_('"'),
      line_no_(1),
      scratch_(NULL),
      scratch_cap_(0),
      scratch_used_(0)
{}

void CsvScanner::destroy()
{
  if (NULL != buf_) {
    free(buf_);
    buf_ = NULL;
  }
  if (NULL != scratch_) {
    free(scratch_);
    scratch_ = NULL;
  }
  buf_cap_ = 0;
  buf_len_ = 0;
  buf_pos_ = 0;
  scratch_cap_ = 0;
  scratch_used_ = 0;
}

int CsvScanner::init(ReadFn read_fn, void *ctx, char delimiter, char quote, char escape)
{
  int ret = FS_OK;
  if (NULL == read_fn) {
    ret = FS_ERR_PARAM;
  } else {
    read_fn_ = read_fn;
    read_ctx_ = ctx;
    delimiter_ = delimiter;
    quote_ = quote;
    escape_ = (0 == escape) ? quote : escape;
    line_no_ = 1;
    buf_cap_ = 256 * 1024;
    buf_ = static_cast<char *>(malloc(buf_cap_));
    scratch_cap_ = 64 * 1024;
    scratch_ = static_cast<char *>(malloc(scratch_cap_));
    if (NULL == buf_ || NULL == scratch_) {
      destroy();
      ret = FS_ERR_BUF_TOO_SMALL;
    } else {
      buf_len_ = 0;
      buf_pos_ = 0;
      scratch_used_ = 0;
    }
  }
  return ret;
}

int CsvScanner::grow_scratch(int64_t need, CSVField *fields, int32_t field_count)
{
  int ret = FS_OK;
  if (need > scratch_cap_) {
    int64_t new_cap = scratch_cap_ * 2;
    while (new_cap < need) {
      new_cap *= 2;
    }
    char *ns = static_cast<char *>(realloc(scratch_, new_cap));
    if (NULL == ns) {
      ret = FS_ERR_BUF_TOO_SMALL;
    } else {
      // fix up field pointers of the current record
      int64_t delta = ns - scratch_;
      for (int32_t i = 0; i < field_count; i++) {
        if (fields[i].quoted && NULL != fields[i].ptr) {
          fields[i].ptr += delta;
        }
      }
      scratch_ = ns;
      scratch_cap_ = new_cap;
    }
  }
  return ret;
}

int CsvScanner::grow_buffer(int64_t need)
{
  int ret = FS_OK;
  if (need > buf_cap_) {
    int64_t new_cap = buf_cap_ * 2;
    while (new_cap < need) {
      new_cap *= 2;
    }
    char *nb = static_cast<char *>(realloc(buf_, new_cap));
    if (NULL == nb) {
      ret = FS_ERR_BUF_TOO_SMALL;
    } else {
      buf_ = nb;
      buf_cap_ = new_cap;
    }
  }
  return ret;
}

int CsvScanner::refill()
{
  int ret = FS_OK;
  if (eof_) {
    ret = FS_ERR_EOF;
  } else {
    int64_t remaining = buf_len_ - buf_pos_;
    if (remaining > 0 && buf_pos_ > 0) {
      MEMMOVE(buf_, buf_ + buf_pos_, static_cast<size_t>(remaining));
    }
    buf_pos_ = 0;
    buf_len_ = remaining;
    if (buf_cap_ - buf_len_ < 64 * 1024) {
      ret = grow_buffer(buf_len_ + 64 * 1024);
    }
    if (FS_OK == ret) {
      const char *chunk = NULL;
      int64_t n = read_fn_(read_ctx_, &chunk);
      if (n <= 0) {
        eof_ = true;
        ret = FS_ERR_EOF; // clean EOF signalled to peek_char callers
        if (n < 0) {
          ret = FS_ERR_DATA;
        }
      } else {
        if (buf_len_ + n > buf_cap_) {
          ret = grow_buffer(buf_len_ + n);
        }
        if (FS_OK == ret) {
          MEMCPY(buf_ + buf_len_, chunk, static_cast<size_t>(n));
          buf_len_ += n;
        }
      }
    }
  }
  return ret;
}

int CsvScanner::peek_char(char &c)
{
  int ret = FS_OK;
  if (buf_pos_ >= buf_len_) {
    ret = refill();
    if (FS_OK == ret) {
      c = buf_[buf_pos_];
    }
  } else {
    c = buf_[buf_pos_];
  }
  return ret;
}

int CsvScanner::skip_bom()
{
  int ret = FS_OK;
  while (buf_len_ - buf_pos_ < 3 && !eof_) {
    if (FS_OK != (ret = refill())) {
      break;
    }
  }
  if (FS_OK == ret && buf_len_ - buf_pos_ >= 3
      && '\xEF' == buf_[buf_pos_] && '\xBB' == buf_[buf_pos_ + 1] && '\xBF' == buf_[buf_pos_ + 2]) {
    buf_pos_ += 3;
  }
  return ret;
}

int CsvScanner::scratch_append(char c, CSVField *fields, int32_t field_count)
{
  int ret = FS_OK;
  if (scratch_used_ + 1 > scratch_cap_) {
    ret = grow_scratch(scratch_used_ + 1, fields, field_count);
  }
  if (FS_OK == ret) {
    scratch_[scratch_used_++] = c;
  }
  return ret;
}

int CsvScanner::consume_newline()
{
  int ret = FS_OK;
  char c = 0;
  if (FS_OK != (ret = peek_char(c))) {
    // EOF after \r is fine
    ret = FS_OK;
  } else if ('\n' == c) {
    buf_pos_++;
  }
  return ret;
}

int CsvScanner::next_record(CSVField *fields, int32_t field_cap, int32_t &field_count)
{
  int ret = FS_OK;
  field_count = 0;
  scratch_used_ = 0;
  if (NULL == buf_ || NULL == fields) {
    ret = FS_ERR_PARAM;
  } else {
    // skip blank lines before the record
    char c = 0;
    while (FS_OK == ret) {
      ret = peek_char(c);
      if (FS_ERR_EOF == ret) {
        ret = FS_ERR_EOF; // clean end, no record
        break;
      } else if (FS_OK != ret) {
        break;
      } else if ('\r' == c || '\n' == c) {
        buf_pos_++;
        if (FS_OK != (ret = consume_newline())) {
          break;
        }
        line_no_++;
      } else {
        ret = FS_OK;
        break; // record content starts
      }
    }
    // parse fields
    while (FS_OK == ret) {
      if (field_count >= field_cap) {
        ret = FS_ERR_BUF_TOO_SMALL;
        break;
      }
      CSVField &f = fields[field_count];
      f.ptr = NULL;
      f.len = 0;
      f.quoted = false;
      int32_t my_idx = field_count;
      field_count++;
      ret = peek_char(c);
      if (FS_ERR_EOF == ret) {
        ret = FS_OK; // trailing empty field at EOF
        break;
      } else if (FS_OK != ret) {
        break;
      } else if (quote_ == c) {
        // quoted field: unescaped content goes to scratch
        f.quoted = true;
        buf_pos_++;
        int64_t content_start = scratch_used_;
        while (FS_OK == ret) {
          char q = 0;
          if (FS_OK != (ret = peek_char(q))) {
            ret = FS_ERR_DATA; // unterminated quote
          } else if (quote_ == q) {
            buf_pos_++;
            char n2 = 0;
            if (FS_OK == peek_char(n2) && quote_ == n2) {
              buf_pos_++;
              ret = scratch_append(quote_, fields, my_idx);
            } else {
              break; // closing quote
            }
          } else if (escape_ != quote_ && escape_ == q) {
            buf_pos_++;
            char e = 0;
            if (FS_OK != (ret = peek_char(e))) {
              ret = FS_ERR_DATA;
            } else {
              buf_pos_++;
              ret = scratch_append(e, fields, my_idx);
            }
          } else if ('\r' == q) {
            // normalize CRLF (and lone CR) inside quoted values to LF,
            // matching DuckDB semantics for windows-authored files
            buf_pos_++;
            char n4 = 0;
            if (FS_OK == peek_char(n4) && '\n' == n4) {
              buf_pos_++;
            }
            line_no_++;
            ret = scratch_append('\n', fields, my_idx);
          } else {
            buf_pos_++;
            if ('\n' == q) {
              line_no_++;
            }
            ret = scratch_append(q, fields, my_idx);
          }
        }
        if (FS_OK == ret) {
          f.ptr = scratch_ + content_start;
          f.len = static_cast<int32_t>(scratch_used_ - content_start);
          // after the closing quote: delimiter, newline or EOF
          char n3 = 0;
          int pr = peek_char(n3);
          if (FS_ERR_EOF == pr) {
            ret = FS_OK;
            break;
          } else if (FS_OK != pr) {
            ret = pr;
          } else if (delimiter_ == n3) {
            buf_pos_++;
            continue; // next field
          } else if ('\r' == n3 || '\n' == n3) {
            buf_pos_++;
            if (FS_OK != (ret = consume_newline())) {
              break;
            }
            line_no_++;
            break;
          } else {
            ret = FS_ERR_DATA;
          }
        }
        break;
      } else {
        // unquoted field: copy content into scratch
        int64_t content_start = scratch_used_;
        while (FS_OK == ret) {
          char u = 0;
          int pr = peek_char(u);
          if (FS_ERR_EOF == pr) {
            ret = FS_OK;
            break;
          } else if (FS_OK != pr) {
            ret = pr;
            break;
          } else if (delimiter_ == u || '\r' == u || '\n' == u) {
            break;
          } else {
            buf_pos_++;
            ret = scratch_append(u, fields, my_idx);
          }
        }
        if (FS_OK == ret) {
          f.ptr = scratch_ + content_start;
          f.len = static_cast<int32_t>(scratch_used_ - content_start);
          char d2 = 0;
          int pr = peek_char(d2);
          if (FS_ERR_EOF == pr) {
            ret = FS_OK;
            break;
          } else if (FS_OK != pr) {
            ret = pr;
            break;
          } else if (delimiter_ == d2) {
            buf_pos_++;
            continue;
          } else {
            // newline
            buf_pos_++;
            if (FS_OK != (ret = consume_newline())) {
              break;
            }
            line_no_++;
            break;
          }
        }
        break;
      }
    }
  }
  return ret;
}

// ---------------------------------------------------------------------------
// type inference
// ---------------------------------------------------------------------------
static bool fs_is_leap(int64_t y)
{
  return (0 == y % 4 && 0 != y % 100) || 0 == y % 400;
}

static bool fs_valid_date(int64_t y, int64_t m, int64_t d)
{
  static const int64_t mdays[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  bool ok = y >= 1 && y <= 9999 && m >= 1 && m <= 12 && d >= 1;
  if (ok) {
    int64_t dim = mdays[m - 1];
    if (2 == m && fs_is_leap(y)) {
      dim = 29;
    }
    ok = d <= dim;
  }
  return ok;
}

static bool fs_digits(const char *s, int32_t len, int64_t &out)
{
  bool ok = len > 0;
  int64_t v = 0;
  for (int32_t i = 0; ok && i < len; i++) {
    if (s[i] < '0' || s[i] > '9') {
      ok = false;
    } else {
      v = v * 10 + (s[i] - '0');
    }
  }
  if (ok) {
    out = v;
  }
  return ok;
}

static bool fs_match_date(const char *s, int32_t len, int64_t &y, int64_t &m, int64_t &d)
{
  // YYYY-MM-DD (exactly 10 chars)
  bool ok = (10 == len) && ('-' == s[4]) && ('-' == s[7]);
  int64_t yy = 0;
  int64_t mm = 0;
  int64_t dd = 0;
  if (ok && fs_digits(s, 4, yy) && fs_digits(s + 5, 2, mm) && fs_digits(s + 8, 2, dd)
      && fs_valid_date(yy, mm, dd)) {
    y = yy;
    m = mm;
    d = dd;
  } else {
    ok = false;
  }
  return ok;
}

static bool fs_match_datetime(const char *s, int32_t len,
                              int64_t &y, int64_t &mo, int64_t &d,
                              int64_t &h, int64_t &mi, int64_t &sec,
                              const char *&frac, int32_t &frac_len, char &sep)
{
  bool ok = false;
  if (len >= 19 && ('-' == s[4]) && ('-' == s[7]) && (' ' == s[10] || 'T' == s[10])
      && (':' == s[13]) && (':' == s[16])) {
    int64_t yy = 0;
    int64_t mm = 0;
    int64_t dd = 0;
    int64_t hh = 0;
    int64_t m2 = 0;
    int64_t ss = 0;
    if (fs_digits(s, 4, yy) && fs_digits(s + 5, 2, mm) && fs_digits(s + 8, 2, dd)
        && fs_digits(s + 11, 2, hh) && fs_digits(s + 14, 2, m2) && fs_digits(s + 17, 2, ss)
        && fs_valid_date(yy, mm, dd) && hh < 24 && m2 < 60 && ss <= 60) {
      ok = true;
      y = yy;
      mo = mm;
      d = dd;
      h = hh;
      mi = m2;
      sec = ss;
      sep = s[10];
      if (len > 19 && '.' == s[19]) {
        frac = s + 20;
        frac_len = len - 20;
        for (int32_t i = 0; ok && i < frac_len; i++) {
          if (frac[i] < '0' || frac[i] > '9') {
            ok = false;
          }
        }
      }
    }
  }
  return ok;
}

FSValueType fs_classify_scalar(const char *s, int32_t len,
                               bool &out_bool, int64_t &out_i, double &out_d)
{
  FSValueType type = FS_VARCHAR;
  out_bool = false;
  out_i = 0;
  out_d = 0;
  if (NULL == s || len <= 0) {
    type = FS_NULL;
  } else if ((4 == len && 0 == fs_strncasecmp_ascii(s, "true", 4))
             || (5 == len && 0 == fs_strncasecmp_ascii(s, "false", 5))) {
    type = FS_BOOLEAN;
    out_bool = (4 == len);
  } else {
    // integer?
    bool is_int = true;
    bool is_double = true;
    int32_t start = ('+' == s[0] || '-' == s[0]) ? 1 : 0;
    if (start >= len) {
      is_int = false;
      is_double = false;
    } else {
      int32_t i = start;
      int32_t int_digits = 0;
      int32_t frac_digits = 0;
      bool has_exp = false;
      bool frac = false;
      for (; i < len; i++) {
        char c = s[i];
        if (c >= '0' && c <= '9') {
          if (frac) {
            frac_digits++;
          } else {
            int_digits++;
          }
        } else if ('.' == c && !frac && !has_exp) {
          frac = true;
          is_int = false; // a fractional point disqualifies the int64 path
        } else if (('e' == c || 'E' == c) && !has_exp && (int_digits > 0 || frac_digits > 0)) {
          has_exp = true;
          if (i + 1 < len && ('+' == s[i + 1] || '-' == s[i + 1])) {
            i++;
          }
          if (i + 1 >= len) {
            is_double = false;
          }
        } else {
          is_int = false;
          is_double = false;
          break;
        }
      }
      if (0 == int_digits && 0 == frac_digits) {
        is_int = false;
        is_double = false;
      }
      if (has_exp) {
        is_int = false;
      }
      if (is_int) {
        // strict int64 range check
        if (len < 32) {
          char tmp[32];
          MEMCPY(tmp, s, len);
          tmp[len] = '\0';
          errno = 0;
          char *endp = NULL;
          int64_t v = FS_STRTOLL(tmp, &endp, 10);
          if (NULL != endp && '\0' == *endp && ERANGE != errno) {
            out_i = v;
            type = FS_BIGINT;
          } else {
            // int64 overflow (or unexpected tail) -> fall back to double
            type = FS_DOUBLE;
            if (len < 64) {
              char tmp2[64];
              MEMCPY(tmp2, s, len);
              tmp2[len] = '\0';
              errno = 0;
              char *endp2 = NULL;
              double d2 = FS_STRTOD(tmp2, &endp2);
              if (NULL != endp2 && '\0' == *endp2 && ERANGE != errno) {
                out_d = d2;
              }
            }
          }
        } else {
          type = FS_DOUBLE;
        }
      } else if (is_double) {
        char tmp[64];
        if (len < 64) {
          MEMCPY(tmp, s, len);
          tmp[len] = '\0';
          errno = 0;
          char *endp = NULL;
          double d = FS_STRTOD(tmp, &endp);
          if (NULL != endp && '\0' == *endp) {
            out_d = d;
            type = FS_DOUBLE;
          } else {
            type = FS_VARCHAR;
          }
        } else {
          type = FS_VARCHAR;
        }
      } else {
        // date / datetime?
        int64_t y = 0;
        int64_t m = 0;
        int64_t d = 0;
        if (fs_match_date(s, len, y, m, d)) {
          type = FS_DATE;
        } else {
          int64_t mo = 0;
          int64_t dd = 0;
          int64_t h = 0;
          int64_t mi = 0;
          int64_t sec = 0;
          const char *frac = NULL;
          int32_t frac_len = 0;
          char sep = ' ';
          if (fs_match_datetime(s, len, y, mo, dd, h, mi, sec, frac, frac_len, sep)) {
            type = FS_DATETIME;
          } else {
            type = FS_VARCHAR;
          }
        }
      }
    }
  }
  return type;
}

FSValueType fs_widen(FSValueType a, FSValueType b)
{
  FSValueType ret = FS_VARCHAR;
  if (a == b) {
    ret = a;
  } else if (FS_NULL == a) {
    ret = b;
  } else if (FS_NULL == b) {
    ret = a;
  } else if ((FS_BIGINT == a && FS_DOUBLE == b) || (FS_DOUBLE == a && FS_BIGINT == b)) {
    ret = FS_DOUBLE;
  } else if ((FS_DATE == a && FS_DATETIME == b) || (FS_DATETIME == a && FS_DATE == b)) {
    ret = FS_DATETIME;
  } else if ((FS_BOOLEAN == a && FS_BIGINT == b) || (FS_BIGINT == a && FS_BOOLEAN == b)) {
    // true/false mix with integers: widen to bigint (true = 1, false = 0)
    ret = FS_BIGINT;
  } else {
    ret = FS_VARCHAR;
  }
  return ret;
}

int fs_parse_typed(FSValueType type, const char *s, int32_t len,
                   bool &b, int64_t &i, double &d,
                   const char *&str, int32_t &str_len)
{
  int ret = FS_OK;
  b = false;
  i = 0;
  d = 0;
  str = s;
  str_len = len;
  switch (type) {
    case FS_NULL:
      break;
    case FS_BOOLEAN: {
      if ((1 == len && '1' == s[0]) || (4 == len && 0 == fs_strncasecmp_ascii(s, "true", 4))) {
        b = true;
      } else if ((1 == len && '0' == s[0]) || (5 == len && 0 == fs_strncasecmp_ascii(s, "false", 5))) {
        b = false;
      } else {
        ret = FS_ERR_DATA;
      }
      break;
    }
    case FS_BIGINT: {
      if (1 == len && '1' == s[0]) {
        b = true;
        i = 1;
      } else if (1 == len && '0' == s[0]) {
        b = false;
        i = 0;
      } else {
        char tmp[32];
        if (len >= 32) {
          ret = FS_ERR_OVERFLOW;
        } else {
          MEMCPY(tmp, s, len);
          tmp[len] = '\0';
          errno = 0;
          char *endp = NULL;
          i = FS_STRTOLL(tmp, &endp, 10);
          if (NULL == endp || '\0' != *endp || ERANGE == errno) {
            ret = FS_ERR_DATA;
          }
        }
      }
      break;
    }
    case FS_DOUBLE: {
      char tmp[64];
      if (len >= 64) {
        ret = FS_ERR_OVERFLOW;
      } else {
        MEMCPY(tmp, s, len);
        tmp[len] = '\0';
        errno = 0;
        char *endp = NULL;
        d = FS_STRTOD(tmp, &endp);
        if (NULL == endp || '\0' != *endp) {
          ret = FS_ERR_DATA;
        }
      }
      break;
    }
    case FS_DATE:
    case FS_DATETIME:
    case FS_VARCHAR:
      // caller keeps the raw text; adapter converts date/datetime text
      str = s;
      str_len = len;
      break;
    default:
      ret = FS_ERR_PARAM;
      break;
  }
  return ret;
}

// ---------------------------------------------------------------------------
// glob matching
// ---------------------------------------------------------------------------
static int glob_match_impl(const char *pat, int32_t plen, int32_t pi,
                           const char *name, int32_t nlen, int32_t ni,
                           bool &matched)
{
  int ret = FS_OK;
  matched = false;
  while (pi < plen) {
    char pc = pat[pi];
    if ('*' == pc) {
      // collapse consecutive stars
      while (pi < plen && '*' == pat[pi]) {
        pi++;
      }
      if (pi >= plen) {
        // trailing star: matches the rest unless we want '/' blocked.
        // DuckDB: '*' does not cross directory separators
        for (int32_t k = ni; k < nlen; k++) {
          if ('/' == name[k]) {
            matched = false;
            return FS_OK;
          }
        }
        matched = true;
        return FS_OK;
      }
      // try every position (not crossing '/')
      for (int32_t k = ni; k <= nlen; k++) {
        bool sub = false;
        if (FS_OK != (ret = glob_match_impl(pat, plen, pi, name, nlen, k, sub))) {
          return ret;
        }
        if (sub) {
          matched = true;
          return FS_OK;
        }
        if (k < nlen && '/' == name[k]) {
          break;
        }
      }
      matched = false;
      return FS_OK;
    } else if ('?' == pc) {
      if (ni >= nlen || '/' == name[ni]) {
        matched = false;
        return FS_OK;
      }
      pi++;
      ni++;
    } else if ('[' == pc) {
      // character class
      int32_t pj = pi + 1;
      bool neg = false;
      if (pj < plen && ('^' == pat[pj] || '!' == pat[pj])) {
        neg = true;
        pj++;
      }
      bool hit = false;
      bool closed = false;
      char c = (ni < nlen) ? name[ni] : '\0';
      int32_t first_idx = pj;
      while (pj < plen) {
        if (']' == pat[pj] && pj > first_idx) {
          closed = true;
          break;
        }
        if (pj + 2 < plen && '-' != pat[pj] && ']' != pat[pj + 2] && '-' == pat[pj + 1]) {
          // range
          if (c >= pat[pj] && c <= pat[pj + 2]) {
            hit = true;
          }
          pj += 3;
        } else {
          if (c == pat[pj]) {
            hit = true;
          }
          pj++;
        }
      }
      if (!closed) {
        // treat '[' literally
        if (ni < nlen && name[ni] == '[') {
          pi++;
          ni++;
        } else {
          matched = false;
          return FS_OK;
        }
      } else {
        if (hit == neg || ni >= nlen) {
          matched = false;
          return FS_OK;
        }
        pi = pj + 1;
        ni++;
      }
    } else {
      if (ni >= nlen || name[ni] != pc) {
        matched = false;
        return FS_OK;
      }
      pi++;
      ni++;
    }
  }
  matched = (ni == nlen);
  return FS_OK;
}

int fs_glob_match(const char *pattern, int32_t plen,
                  const char *name, int32_t nlen, bool &matched)
{
  return glob_match_impl(pattern, plen, 0, name, nlen, 0, matched);
}

// ---------------------------------------------------------------------------
// CSV output helpers
// ---------------------------------------------------------------------------
static int csv_out_need_quote(const char *field, int32_t len,
                              char delimiter, char quote,
                              const char *nullstr, int32_t nullstr_len)
{
  bool need = false;
  for (int32_t i = 0; !need && i < len; i++) {
    if (delimiter == field[i] || quote == field[i] || '\n' == field[i] || '\r' == field[i]) {
      need = true;
    }
  }
  if (!need && NULL != nullstr && nullstr_len > 0 && len == nullstr_len
      && 0 == MEMCMP(field, nullstr, len)) {
    need = true;
  }
  return need;
}

int fs_csv_append_field(char *dst, int64_t dst_cap, int64_t &dst_len,
                        const char *field, int32_t field_len,
                        char delimiter, char quote, const char *nullstr,
                        int32_t nullstr_len, bool is_null)
{
  int ret = FS_OK;
  if (is_null) {
    if (dst_len + nullstr_len > dst_cap) {
      ret = FS_ERR_BUF_TOO_SMALL;
    } else if (nullstr_len > 0) {
      MEMCPY(dst + dst_len, nullstr, nullstr_len);
      dst_len += nullstr_len;
    }
  } else if (csv_out_need_quote(field, field_len, delimiter, quote, nullstr, nullstr_len)) {
    // quoted with doubled quotes
    int64_t need = field_len + 2;
    for (int32_t i = 0; i < field_len; i++) {
      if (quote == field[i]) {
        need++;
      }
    }
    if (dst_len + need > dst_cap) {
      ret = FS_ERR_BUF_TOO_SMALL;
    } else {
      dst[dst_len++] = quote;
      for (int32_t i = 0; i < field_len; i++) {
        if (quote == field[i]) {
          dst[dst_len++] = quote;
        }
        dst[dst_len++] = field[i];
      }
      dst[dst_len++] = quote;
    }
  } else {
    if (dst_len + field_len > dst_cap) {
      ret = FS_ERR_BUF_TOO_SMALL;
    } else {
      MEMCPY(dst + dst_len, field, field_len);
      dst_len += field_len;
    }
  }
  return ret;
}

int fs_format_value(FSValueType type, bool b, int64_t i, double d,
                    const char *str, int32_t str_len,
                    char *out, int64_t out_cap, int32_t &out_len)
{
  int ret = FS_OK;
  out_len = 0;
  switch (type) {
    case FS_BOOLEAN:
      if (out_cap < (b ? 4 : 5)) {
        ret = FS_ERR_BUF_TOO_SMALL;
      } else if (b) {
        MEMCPY(out, "true", 4);
        out_len = 4;
      } else {
        MEMCPY(out, "false", 5);
        out_len = 5;
      }
      break;
    case FS_BIGINT: {
      int n = FS_SNPRINTF(out, out_cap, "%lld", static_cast<long long>(i));
      if (n < 0 || n >= out_cap) {
        ret = FS_ERR_BUF_TOO_SMALL;
      } else {
        out_len = n;
      }
      break;
    }
    case FS_DOUBLE: {
      // shortest representation: try %g-like formatting; use 15 significant
      // digits then trim trailing zeros
      char tmp[64];
      int n = FS_SNPRINTF(tmp, sizeof(tmp), "%.15g", d);
      if (n < 0 || n >= static_cast<int>(sizeof(tmp))) {
        ret = FS_ERR_BUF_TOO_SMALL;
      } else if (n >= out_cap) {
        ret = FS_ERR_BUF_TOO_SMALL;
      } else {
        MEMCPY(out, tmp, n);
        out_len = n;
      }
      break;
    }
    case FS_VARCHAR:
    case FS_DATE:
    case FS_DATETIME:
    case FS_NULL:
      if (str_len > out_cap) {
        ret = FS_ERR_BUF_TOO_SMALL;
      } else {
        MEMCPY(out, str, str_len);
        out_len = str_len;
      }
      break;
    default:
      ret = FS_ERR_PARAM;
      break;
  }
  return ret;
}


/**
 * Convert days since 1970-01-01 (parquet DATE) to "YYYY-MM-DD".
 * (Howard Hinnant's civil_from_days algorithm)
 */
int fs_days_to_date_str(int64_t days, char *out, int64_t out_cap, int64_t &out_len)
{
  int ret = FS_OK;
  out_len = 0;
  if (NULL == out || out_cap < 11) {
    ret = FS_ERR_BUF_TOO_SMALL;
  } else {
    days += 719468;
    int64_t era = (days >= 0 ? days : days - 146096) / 146097;
    uint64_t doe = static_cast<uint64_t>(days - era * 146097);
    uint64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    int64_t y = static_cast<int64_t>(yoe) + era * 400;
    uint64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    uint64_t mp = (5 * doy + 2) / 153;
    uint64_t dd = doy - (153 * mp + 2) / 5 + 1;
    uint64_t mm = mp < 10 ? mp + 3 : mp - 9;
    if (mm <= 2) {
      ++y;
    }
    out_len = FS_SNPRINTF(out, out_cap, "%04ld-%02ld-%02ld",
                          static_cast<long>(y),
                          static_cast<long>(mm),
                          static_cast<long>(dd));
    if (out_len < 0) {
      ret = FS_ERR_BUF_TOO_SMALL;
    }
  }
  return ret;
}

/**
 * Convert milliseconds since 1970-01-01 (parquet TIMESTAMP_MILLIS) to
 * "YYYY-MM-DD HH:MM:SS.mmm".
 */
int fs_millis_to_datetime_str(int64_t millis, char *out, int64_t out_cap, int64_t &out_len)
{
  int ret = FS_OK;
  out_len = 0;
  if (NULL == out || out_cap < 24) {
    ret = FS_ERR_BUF_TOO_SMALL;
  } else {
    int64_t secs = millis / 1000;
    int64_t ms = millis % 1000;
    if (ms < 0) {
      ms += 1000;
      --secs;
    }
    int64_t days = secs / 86400;
    int64_t sod = secs % 86400;
    if (sod < 0) {
      sod += 86400;
      --days;
    }
    char date_buf[16];
    int64_t date_len = 0;
    if (FS_OK != (ret = fs_days_to_date_str(days, date_buf, sizeof(date_buf), date_len))) {
      // nothing
    } else {
      out_len = FS_SNPRINTF(out, out_cap, "%s %02ld:%02ld:%02ld.%03ld",
                            date_buf,
                            static_cast<long>(sod / 3600),
                            static_cast<long>((sod / 60) % 60),
                            static_cast<long>(sod % 60),
                            static_cast<long>(ms));
      if (out_len < 0) {
        ret = FS_ERR_BUF_TOO_SMALL;
      }
    }
  }
  return ret;
}

} // end namespace file_sql
} // end namespace oceanbase
