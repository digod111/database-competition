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

#pragma once

/**
 * Pure algorithm core for "file as database" (file_sql) feature.
 *
 * This file deliberately depends on nothing but C standard headers so the
 * byte level algorithms (snappy, thrift-compact, parquet decoding, CSV
 * record scanning, type inference and glob matching) can be tested in
 * isolation. The OceanBase integration layer lives in
 * share/file_sql/ob_file_scan_utils.h.
 */

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifndef MEMCPY
#define MEMCPY memcpy
#endif
#ifndef MEMSET
#define MEMSET memset
#endif
#ifndef MEMMOVE
#define MEMMOVE memmove
#endif
#ifndef MEMCMP
#define MEMCMP memcmp
#endif

#define FS_SNPRINTF snprintf
#define FS_STRTOLL strtoll
#define FS_STRTOD strtod

// case-insensitive ascii comparison used for true/false tokens
static inline int fs_strncasecmp_ascii(const char *a, const char *b, int32_t len)
{
  int r = 0;
  for (int32_t i = 0; 0 == r && i < len; i++) {
    char ca = a[i];
    char cb = b[i];
    if (ca >= 'A' && ca <= 'Z') {
      ca += 'a' - 'A';
    }
    if (cb >= 'A' && cb <= 'Z') {
      cb += 'a' - 'A';
    }
    r = static_cast<int>(static_cast<unsigned char>(ca)) - static_cast<int>(static_cast<unsigned char>(cb));
  }
  return r;
}

namespace oceanbase
{
namespace file_sql
{

// ---------------------------------------------------------------------------
// error codes local to this module (negative values, never collide with OB)
// ---------------------------------------------------------------------------
enum FSErrorCode
{
  FS_OK = 0,
  FS_ERR_DATA = -90001,        // malformed file content
  FS_ERR_EOF = -90002,         // no more data
  FS_ERR_BUF_TOO_SMALL = -90003,
  FS_ERR_UNSUPPORTED = -90004, // unsupported feature in file
  FS_ERR_OVERFLOW = -90005,
  FS_ERR_PARAM = -90006,
};

// ---------------------------------------------------------------------------
// deterministic value types used for schema inference
// ---------------------------------------------------------------------------
enum FSValueType
{
  FS_NULL = 0,
  FS_BOOLEAN,
  FS_BIGINT,
  FS_DOUBLE,
  FS_VARCHAR,
  FS_DATE,
  FS_DATETIME,
};

const char *fs_value_type_name(FSValueType type);

// ---------------------------------------------------------------------------
// minimal bump allocator (all core allocations flow through this)
// ---------------------------------------------------------------------------
struct FSArena
{
public:
  FSArena() : buf_(NULL), cap_(0), used_(0) {}
  ~FSArena() { reset(); }
  int init(int64_t cap);
  void reset();
  char *alloc(int64_t size);
  char *alloc_zero(int64_t size);
  // duplicate a byte range (NUL terminated for convenience)
  char *strdup_range(const char *p, int64_t len);
private:
  char *buf_;
  int64_t cap_;
  int64_t used_;
};

// ---------------------------------------------------------------------------
// snappy raw-format decompression
// ---------------------------------------------------------------------------
int fs_snappy_decompress(const char *in, int64_t in_len,
                         char *out, int64_t out_cap, int64_t &out_len);

// ---------------------------------------------------------------------------
// thrift compact protocol reader (subset used by parquet metadata)
// ---------------------------------------------------------------------------
struct FSThriftReader
{
public:
  FSThriftReader(const char *data, int64_t len);
  bool at_end() const { return p_ >= end_; }
  int read_byte(uint8_t &b);
  int read_varint(uint64_t &v);
  int read_zigzag(int64_t &v);
  int read_string(const char *&str, int32_t &len);
  // reads a field header. Returns field id, sets type (thrift compact type).
  // stop_field is signalled with type 0. last_fid is updated in place so the
  // caller can keep passing the same tracker variable.
  int read_field_begin(int16_t &last_fid, int16_t &fid, uint8_t &type);
  // skip a value of the given compact type (types: 1=BOOL_TRUE 2=BOOL_FALSE
  // 3=BYTE 4=I16 5=I32 6=I64 7=DOUBLE 8=BINARY/STRING 9=LIST 10=SET 11=MAP 12=STRUCT)
  int skip_value(uint8_t type);
  int read_i16(int16_t &v);
  int read_i32(int32_t &v);
  int read_i64(int64_t &v);
  int read_double(double &v);
  const char *p_;
  const char *end_;
};

// ---------------------------------------------------------------------------
// RLE / bit-packed hybrid decoder (parquet levels and dictionary indices)
// ---------------------------------------------------------------------------
struct FSRleDecoder
{
public:
  FSRleDecoder();
  int init(const char *data, int64_t len, int32_t bit_width);
  // decode at most out_cap values, returns decoded count
  int64_t decode(int32_t *out, int64_t out_cap);
  // true if the last decode hit a malformed run
  bool has_error() const { return err_; }
private:
  int read_uleb128(uint64_t &v);
  int load_next_run();
  const uint8_t *data_;
  const uint8_t *end_;
  int32_t bit_width_;
  // run state
  bool in_run_;            // true if a run header has been read
  bool literal_run_;       // current run is bit-packed literal
  bool err_;               // set when a malformed run is encountered
  int64_t rle_left_;       // values left in current rle run
  int64_t literal_left_;   // values left in current literal run
  uint32_t rle_value_;     // current rle repeated value
  // literal run bit reader: bits are packed contiguously LSB-first from
  // run_start_; the run occupies ceil(count * bit_width / 8) bytes
  const uint8_t *run_start_;
  const uint8_t *run_end_;
  int64_t run_bit_pos_;    // bit offset within the current literal run
};

// ---------------------------------------------------------------------------
// parquet metadata + decoding
// ---------------------------------------------------------------------------
enum PQType
{
  PQ_BOOLEAN = 0,
  PQ_INT32 = 1,
  PQ_INT64 = 2,
  PQ_INT96 = 3,
  PQ_FLOAT = 4,
  PQ_DOUBLE = 5,
  PQ_BYTE_ARRAY = 6,
  PQ_FIXED_LEN_BYTE_ARRAY = 7,
};

enum PQConvertedType
{
  PQC_NONE = -1,
  PQC_UTF8 = 0,
  PQC_DATE = 6,
  PQC_TIMESTAMP_MILLIS = 9,
  PQC_TIMESTAMP_MICROS = 10,
  PQC_INT_8 = 15,
  PQC_INT_16 = 16,
  PQC_INT_32 = 17,
  PQC_INT_64 = 18,
  PQC_DECIMAL = 5,
};

enum PQCodec
{
  PQ_CODEC_UNCOMPRESSED = 0,
  PQ_CODEC_SNAPPY = 1,
};

// one leaf column in the parquet schema
struct PQColumn
{
  const char *name;
  int32_t name_len;
  int32_t physical_type;
  int32_t converted_type;
  int32_t scale;
  int32_t precision;
  int32_t type_length;
  int32_t max_def_level;
  int32_t max_rep_level;
};

struct PQColumnChunk
{
  int32_t physical_type;
  int32_t codec;
  int64_t num_values;
  int64_t data_page_offset;
  int64_t dict_page_offset;
  int64_t total_compressed_size;
  int32_t leaf_index; // index into PQFileMeta::columns
};

struct PQRowGroup
{
  int64_t num_rows;
  int64_t base_row;   // cumulative row offset of this row group
  PQColumnChunk *chunks;
  int32_t chunk_count;
};

struct PQFileMeta
{
  int64_t num_rows;
  PQColumn *columns;
  int32_t column_count;
  PQRowGroup *row_groups;
  int32_t row_group_count;
};

/**
 * Parse the parquet footer. `data` must reference the whole file buffer.
 * All returned structures point into `arena`.
 */
int pq_parse_footer(const char *data, int64_t len, FSArena &arena, PQFileMeta &meta);

/**
 * Find the column chunk of a leaf column inside a row group.
 */
int pq_find_chunk(const PQRowGroup &rg, int32_t leaf_index, PQColumnChunk &out);

/**
 * Decoded values of one column chunk (whole chunk, all pages).
 * Arrays are allocated from `arena`. String values reference either the
 * raw file buffer or an arena-owned decompressed page buffer, both of
 * which remain valid while the scanner works on the current chunk.
 */
struct FSStr
{
  const char *ptr;
  int32_t len;
};

struct FSColumnData
{
  FSValueType value_type;
  int64_t count;
  // non-null flag per value (1 = valid)
  uint8_t *not_null;
  // typed buffers (only the one matching value_type is meaningful)
  uint8_t *bools;
  int64_t *ints;
  double *dbls;
  FSStr *strs;
  // for DATE/DATETIME ints holds days-since-epoch / micros-since-epoch
};

int pq_read_column_chunk(const char *data, int64_t file_len,
                         const PQColumn &col, const PQColumnChunk &chunk,
                         FSArena &arena, FSColumnData &out);

// ---------------------------------------------------------------------------
// CSV
// ---------------------------------------------------------------------------
struct CSVField
{
  const char *ptr;
  int32_t len;
  bool quoted; // true if the field was quoted; unquoted empty means NULL
};

/**
 * Streaming CSV record scanner.
 *
 * The scanner pulls bytes from a caller supplied reader, keeps a working
 * buffer so records spanning refills are handled transparently, and hands
 * out complete records. Field pointers reference the internal buffer and
 * stay valid until the next next_record() call.
 */
class CsvScanner
{
public:
  // read fn must fill buf/len with the next chunk and return the byte count
  // (0 means EOF). It may reuse the buffer between calls.
  typedef int64_t (*ReadFn)(void *ctx, const char **buf);
  CsvScanner();
  ~CsvScanner() { destroy(); }
  void destroy();
  int init(ReadFn read_fn, void *ctx, char delimiter, char quote, char escape);
  /**
   * Parse the next record. Returns FS_OK on success, FS_ERR_EOF at clean
   * end of file, FS_ERR_DATA for a malformed record.
   * `fields`/`field_cap` is caller storage; on overflow the function
   * returns FS_ERR_BUF_TOO_SMALL.
   */
  int next_record(CSVField *fields, int32_t field_cap, int32_t &field_count);
  int64_t current_line() const { return line_no_; }
  // skip UTF-8 BOM if present at file start
  int skip_bom();
private:
  int refill();
  int peek_char(char &c);
  int grow_buffer(int64_t need);
  int grow_scratch(int64_t need, CSVField *fields, int32_t field_count);
  int scratch_append(char c, CSVField *fields, int32_t field_count);
  int consume_newline();
  ReadFn read_fn_;
  void *read_ctx_;
  char *buf_;
  int64_t buf_cap_;
  int64_t buf_len_;   // valid bytes in buffer
  int64_t buf_pos_;   // parse position
  bool eof_;
  char delimiter_;
  char quote_;
  char escape_;
  int64_t line_no_;
  // scratch to unescape fields (valid until next record)
  char *scratch_;
  int64_t scratch_cap_;
  int64_t scratch_used_;
};

/**
 * Classify one CSV/JSONL scalar token into the deterministic type system.
 * For FS_DATE/FS_DATETIME validity is checked (calendar correct).
 * For FS_BIGINT the value must fit int64_t, otherwise FS_DOUBLE.
 */
FSValueType fs_classify_scalar(const char *s, int32_t len,
                               bool &out_bool, int64_t &out_i, double &out_d);

/** widen two inferred types into the result column type */
FSValueType fs_widen(FSValueType a, FSValueType b);

/** parse a typed value of the given column type (strict) */
int fs_parse_typed(FSValueType type, const char *s, int32_t len,
                   bool &b, int64_t &i, double &d,
                   const char *&str, int32_t &str_len);

// ---------------------------------------------------------------------------
// glob matching (*, ?, [class]) with '/' never matched by wildcards
// ---------------------------------------------------------------------------
int fs_glob_match(const char *pattern, int32_t plen,
                  const char *name, int32_t nlen, bool &matched);

// ---------------------------------------------------------------------------
// CSV output (used by COPY TO) - append one field with DuckDB style quoting
// ---------------------------------------------------------------------------
int fs_csv_append_field(char *dst, int64_t dst_cap, int64_t &dst_len,
                        const char *field, int32_t field_len,
                        char delimiter, char quote, const char *nullstr,
                        int32_t nullstr_len, bool is_null);

/**
 * Format a typed value into CSV text representation.
 * doubles: shortest round-trip representation (e.g. 98.5, 87 as integer when
 * integral values were written from BIGINT source - handled by caller).
 */
int fs_format_value(FSValueType type, bool b, int64_t i, double d,
                    const char *str, int32_t str_len,
                    char *out, int64_t out_cap, int32_t &out_len);

/**
 * Convert days since 1970-01-01 (parquet DATE) to "YYYY-MM-DD".
 * Writes out_len characters (10).
 */
int fs_days_to_date_str(int64_t days, char *out, int64_t out_cap, int64_t &out_len);

/**
 * Convert milliseconds since 1970-01-01 (parquet TIMESTAMP_MILLIS) to
 * "YYYY-MM-DD HH:MM:SS.mmm" (23 chars).
 */
int fs_millis_to_datetime_str(int64_t millis, char *out, int64_t out_cap, int64_t &out_len);

} // end namespace file_sql
} // end namespace oceanbase
