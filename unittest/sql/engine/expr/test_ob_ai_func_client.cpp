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
#include <cstdlib>
#include <string>
#include "sql/engine/expr/ob_expr_ai/ob_ai_func_client.h"

using namespace oceanbase::common;

namespace
{
// Opt in with --gtest_also_run_disabled_tests after starting the loopback fixture.
class AIFuncClientTest : public ::testing::Test
{
protected:
  ObArenaAllocator allocator_;
  ObAIFuncClient client_;
  std::string base_;

  void SetUp() override
  {
    const char *base = std::getenv("SEEKDB_AI_MOCK_URL");
    ASSERT_NE(nullptr, base) << "Start ai_mock_service.py and set SEEKDB_AI_MOCK_URL";
    base_ = base;
  }

  ObJsonObject *request()
  {
    ObIJsonBase *value = nullptr;
    ObString json(R"({"model":"any-model","input":["first","second"],"dimensions":4})");
    int ret = ObJsonBaseFactory::get_json_base(&allocator_, json,
        ObJsonInType::JSON_TREE, ObJsonInType::JSON_TREE, value);
    EXPECT_EQ(OB_SUCCESS, ret);
    return ret == OB_SUCCESS ? static_cast<ObJsonObject *>(value) : nullptr;
  }

  int send(const char *path, ObJsonObject *&response)
  {
    std::string address = base_ + path;
    ObString url(address.c_str());
    ObArray<ObString> headers;
    int ret = headers.push_back(ObString("Content-Type: application/json"));
    return ret == OB_SUCCESS ? client_.send_post(allocator_, url, headers, request(), response) : ret;
  }
};

TEST_F(AIFuncClientTest, DISABLED_RejectsNonObjectJsonResponse)
{
  ObJsonObject *response = nullptr;
  EXPECT_EQ(OB_INVALID_DATA, send("/v1/embeddings?fault=non_object", response));
  EXPECT_EQ(nullptr, response);
}

TEST_F(AIFuncClientTest, DISABLED_InvalidJsonResponseDoesNotReturnStaleOutput)
{
  ObJsonObject *response = request();
  EXPECT_EQ(OB_ERR_INVALID_JSON_TEXT, send("/v1/embeddings?fault=invalid_json", response));
  EXPECT_EQ(nullptr, response);
}

TEST_F(AIFuncClientTest, DISABLED_ReadsCompleteEmbeddingResponse)
{
  ObJsonObject *response = nullptr;
  ASSERT_EQ(OB_SUCCESS, send("/v1/embeddings", response));
  ASSERT_NE(nullptr, response);
  ASSERT_NE(nullptr, response->get_value("data"));
  EXPECT_EQ(2, response->get_value("data")->element_count());
}

TEST_F(AIFuncClientTest, DISABLED_ExhaustedBatchRetriesReturnFailure)
{
  std::string address = base_ + "/v1/embeddings?fault=http_error";
  ObString url(address.c_str());
  ObArray<ObString> headers;
  ASSERT_EQ(OB_SUCCESS, headers.push_back(ObString("Content-Type: application/json")));
  ObArray<ObJsonObject *> requests, responses;
  ASSERT_EQ(OB_SUCCESS, requests.push_back(request()));
  EXPECT_NE(OB_SUCCESS, client_.send_post_batch(allocator_, url, headers, requests, responses));
  EXPECT_EQ(0, responses.count());
}

TEST_F(AIFuncClientTest, DISABLED_PartialBatchInitializationAndResetAreSafe)
{
  std::string address = base_ + "/v1/embeddings";
  ObString url(address.c_str());
  ObArray<ObString> headers;
  ASSERT_EQ(OB_SUCCESS, headers.push_back(ObString("Content-Type: application/json")));
  ASSERT_EQ(OB_SUCCESS, client_.init(allocator_, url, headers));
  ObArray<ObJsonObject *> requests;
  ASSERT_EQ(OB_SUCCESS, requests.push_back(request()));
  ASSERT_EQ(OB_SUCCESS, requests.push_back(nullptr));
  EXPECT_EQ(OB_INVALID_ARGUMENT, client_.send_post_batch_no_wait(requests));
  client_.reset();
}

TEST_F(AIFuncClientTest, DISABLED_ResetReleasesPendingBatchBeforeDroppingAllocator)
{
  std::string address = base_ + "/v1/embeddings";
  ObString url(address.c_str());
  ObArray<ObString> headers;
  ASSERT_EQ(OB_SUCCESS, headers.push_back(ObString("Content-Type: application/json")));
  ASSERT_EQ(OB_SUCCESS, client_.init(allocator_, url, headers));
  ObArray<ObJsonObject *> requests;
  ASSERT_EQ(OB_SUCCESS, requests.push_back(request()));
  ASSERT_EQ(OB_SUCCESS, client_.send_post_batch_no_wait(requests));
  client_.reset();
}
} // namespace
