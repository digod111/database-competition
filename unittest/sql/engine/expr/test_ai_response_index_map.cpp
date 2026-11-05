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

#include <gtest/gtest.h>
#include <algorithm>
#include <cstdint>
#include <limits>
#include <numeric>
#include <vector>
#include "sql/engine/expr/ob_expr_ai/ai_response_index_map.h"

using oceanbase::common::AiResponseIndexMap;

TEST(AiResponseIndexMap, RestoresRequestOrder)
{
  int64_t positions[4];
  AiResponseIndexMap map(4, positions);
  const int64_t response_indices[] = {3, 1, 0, 2};
  const int64_t response_values[] = {40, 20, 10, 30};
  for (int64_t i = 0; i < 4; ++i) { ASSERT_TRUE(map.record(i, response_indices[i])); }
  ASSERT_TRUE(map.complete());
  for (int64_t i = 0; i < 4; ++i) {
    EXPECT_EQ((i + 1) * 10, response_values[map.response_position(i)]);
  }
}

TEST(AiResponseIndexMap, RejectsDuplicateAndIncompleteResponses)
{
  int64_t positions[3];
  AiResponseIndexMap map(3, positions);
  EXPECT_FALSE(map.complete());
  ASSERT_TRUE(map.record(0, 2));
  EXPECT_FALSE(map.record(1, 2));
  EXPECT_EQ(0, map.response_position(2));
  EXPECT_EQ(-1, map.response_position(0));
  EXPECT_FALSE(map.complete());
  ASSERT_TRUE(map.record(1, 0));
  EXPECT_FALSE(map.complete());
  ASSERT_TRUE(map.record(2, 1));
  EXPECT_TRUE(map.complete());
}

TEST(AiResponseIndexMap, RejectsInvalidRequestAndResponsePositions)
{
  int64_t positions[2];
  AiResponseIndexMap map(2, positions);
  const int64_t invalid[] = {-1, 2, std::numeric_limits<int64_t>::min(),
                            std::numeric_limits<int64_t>::max()};
  for (int64_t value : invalid) {
    EXPECT_FALSE(map.record(0, value));
    EXPECT_FALSE(map.record(value, 0));
    EXPECT_EQ(-1, map.response_position(value));
  }
  EXPECT_FALSE(map.complete());
  EXPECT_TRUE(map.record(0, 0));
  EXPECT_TRUE(map.record(1, 1));
  EXPECT_TRUE(map.complete());
}

TEST(AiResponseIndexMap, HandlesEmptyOrUnavailableStorage)
{
  int64_t position = 42;
  AiResponseIndexMap empty(0, &position);
  EXPECT_FALSE(empty.record(0, 0));
  EXPECT_FALSE(empty.complete());
  EXPECT_EQ(42, position);
  AiResponseIndexMap unavailable(2, nullptr);
  EXPECT_FALSE(unavailable.record(0, 0));
  EXPECT_FALSE(unavailable.complete());
  EXPECT_EQ(-1, unavailable.response_position(0));
}

TEST(AiResponseIndexMap, EveryPermutationThroughEightInputsRestoresExactly)
{
  for (int64_t count = 1; count <= 8; ++count) {
    std::vector<int64_t> permutation(count);
    std::iota(permutation.begin(), permutation.end(), 0);
    do {
      std::vector<int64_t> positions(count);
      AiResponseIndexMap map(count, positions.data());
      for (int64_t response = 0; response < count; ++response) {
        ASSERT_TRUE(map.record(response, permutation[response]));
      }
      ASSERT_TRUE(map.complete());
      for (int64_t request = 0; request < count; ++request) {
        ASSERT_EQ(request, permutation[map.response_position(request)]);
      }
    } while (std::next_permutation(permutation.begin(), permutation.end()));
  }
}
