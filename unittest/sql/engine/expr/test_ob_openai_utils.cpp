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
#include <string>
#include "sql/engine/expr/ob_expr_ai/ob_ai_func_utils.h"

using namespace oceanbase::common;

namespace
{
class AIFuncUtilsTest : public ::testing::Test
{
protected:
  ObArenaAllocator allocator_;

  ObJsonObject *object(const char *json)
  {
    ObIJsonBase *value = nullptr;
    ObString text(json);
    const int ret = ObJsonBaseFactory::get_json_base(&allocator_, text,
        ObJsonInType::JSON_TREE, ObJsonInType::JSON_TREE, value);
    EXPECT_EQ(OB_SUCCESS, ret);
    if (ret != OB_SUCCESS || value == nullptr || value->json_type() != ObJsonNodeType::J_OBJECT) {
      ADD_FAILURE() << "invalid test fixture";
      return nullptr;
    }
    return static_cast<ObJsonObject *>(value);
  }
};

TEST_F(AIFuncUtilsTest, EmbeddingRestoresRequestOrder)
{
  ObOpenAIUtils::ObOpenAIEmbed provider;
  ObIJsonBase *result = nullptr;
  ASSERT_EQ(OB_SUCCESS, provider.parse_output(allocator_, object(
      R"({"data":[{"index":1,"embedding":[3,4]},{"index":0,"embedding":[1,2]}]})"), result));
  ASSERT_NE(nullptr, result);
  auto *rows = static_cast<ObJsonArray *>(result);
  ASSERT_EQ(2, rows->element_count());
  auto *first = static_cast<ObJsonArray *>(rows->get_value(0));
  auto *second = static_cast<ObJsonArray *>(rows->get_value(1));
  EXPECT_EQ(1, first->get_value(0)->get_int());
  EXPECT_EQ(3, second->get_value(0)->get_int());
}

TEST_F(AIFuncUtilsTest, EmbeddingRejectsInvalidShapeValuesAndIndices)
{
  const char *invalid[] = {
    R"({"data":{}})",
    R"({"data":[]})",
    R"({"data":[null]})",
    R"({"data":[{"embedding":[1]}]})",
    R"({"data":[{"index":-1,"embedding":[1]}]})",
    R"({"data":[{"index":0.5,"embedding":[1]}]})",
    R"({"data":[{"index":"0","embedding":[1]}]})",
    R"({"data":[{"index":1,"embedding":[1]}]})",
    R"({"data":[{"index":0,"embedding":[]}]})",
    R"({"data":[{"index":0,"embedding":["1"]}]})",
    R"({"data":[{"index":0,"embedding":[null]}]})",
    R"({"data":[{"index":0,"embedding":[true]}]})",
    R"({"data":[{"index":0,"embedding":[1]},{"index":0,"embedding":[2]}]})",
    R"({"data":[{"index":0,"embedding":[1]},{"index":1,"embedding":[2,3]}]})"
  };
  ObOpenAIUtils::ObOpenAIEmbed provider;
  for (const char *json : invalid) {
    SCOPED_TRACE(json);
    ObIJsonBase *result = nullptr;
    EXPECT_NE(OB_SUCCESS, provider.parse_output(allocator_, object(json), result));
    EXPECT_EQ(nullptr, result);
  }
}

TEST_F(AIFuncUtilsTest, CompletionRequiresNonemptyText)
{
  const char *invalid[] = {
    R"({})", R"({"choices":[]})",
    R"({"choices":[{"message":{"content":null}}]})",
    R"({"choices":[{"message":{"content":""}}]})",
    R"({"choices":[{"message":{"content":{}}}]})"
  };
  ObOpenAIUtils::ObOpenAIComplete provider;
  for (const char *json : invalid) {
    SCOPED_TRACE(json);
    ObIJsonBase *result = nullptr;
    EXPECT_NE(OB_SUCCESS, provider.parse_output(allocator_, object(json), result));
  }
  ObIJsonBase *result = nullptr;
  ASSERT_EQ(OB_SUCCESS, provider.parse_output(allocator_, object(
      R"({"choices":[{"message":{"content":"你好"}}]})"), result));
  EXPECT_EQ(ObJsonNodeType::J_STRING, result->json_type());
}

TEST_F(AIFuncUtilsTest, OllamaUsesNativeNonstreamingRequest)
{
  ObOllamaUtils::ObOllamaComplete provider;
  ObString model("test-model"), system, prompt("hello");
  ObJsonObject *body = nullptr;
  ASSERT_EQ(OB_SUCCESS, provider.get_body(allocator_, model, system, prompt,
      object(R"({"temperature":0.2,"max_tokens":16})"), body));
  ASSERT_NE(nullptr, body->get_value("stream"));
  EXPECT_FALSE(body->get_value("stream")->get_boolean());
  ASSERT_NE(nullptr, body->get_value("options"));
  auto *options = static_cast<ObJsonObject *>(body->get_value("options"));
  EXPECT_EQ(16, options->get_value("num_predict")->get_int());
  double temperature = 0;
  ASSERT_EQ(OB_SUCCESS, options->get_value("temperature")->to_double(temperature));
  EXPECT_DOUBLE_EQ(0.2, temperature);
  ObArray<ObString> headers;
  ObString empty_key;
  EXPECT_EQ(OB_SUCCESS, provider.get_header(allocator_, empty_key, headers));
  EXPECT_FALSE(headers.empty());
}

TEST_F(AIFuncUtilsTest, CompletionAcceptsUnsignedIntegerTokenLimit)
{
  ObOpenAIUtils::ObOpenAIComplete provider;
  ObJsonObject *config = object("{}");
  auto *tokens = OB_NEWx(ObJsonUint, &allocator_, static_cast<uint64_t>(16));
  ASSERT_NE(nullptr, tokens);
  ASSERT_EQ(OB_SUCCESS, config->add("max_tokens", tokens));
  ObString model("any-model"), system, prompt("hello");
  ObJsonObject *body = nullptr;
  ASSERT_EQ(OB_SUCCESS, provider.get_body(allocator_, model, system, prompt, config, body));
  EXPECT_EQ(16U, body->get_value("max_tokens")->get_uint());
}

TEST_F(AIFuncUtilsTest, CompletionRejectsInvalidGenerationConfig)
{
  const char *invalid[] = {R"({"temperature":-0.1})", R"({"temperature":"0.2"})",
      R"({"max_tokens":0})", R"({"max_tokens":-1})", R"({"max_tokens":1.5})"};
  ObOpenAIUtils::ObOpenAIComplete provider;
  ObString model("any-model"), system, prompt("hello");
  for (const char *json : invalid) {
    SCOPED_TRACE(json);
    ObJsonObject *body = nullptr;
    EXPECT_NE(OB_SUCCESS, provider.get_body(allocator_, model, system, prompt, object(json), body));
  }
}

TEST_F(AIFuncUtilsTest, OllamaRejectsMalformedEmbeddings)
{
  ObOllamaUtils::ObOllamaEmbed provider;
  const char *invalid[] = {R"({"embeddings":{}})", R"({"embeddings":[[]]})",
      R"({"embeddings":[[1],[2,3]]})", R"({"embeddings":[["1"]]})"};
  for (const char *json : invalid) {
    SCOPED_TRACE(json);
    ObIJsonBase *result = nullptr;
    EXPECT_NE(OB_SUCCESS, provider.parse_output(allocator_, object(json), result));
  }
}

TEST_F(AIFuncUtilsTest, NestedPromptUsesJsonAndRepeatsWithoutReparsingArguments)
{
  ObString result;
  ASSERT_EQ(OB_SUCCESS, ObAIFuncPromptObjectUtils::replace_all_str_args_in_template(allocator_,
      object(R"({"template":"{0}/{0}/{1}","args":[{"template":"你好{0}","args":["{1}"]},[1,true]]})"), result));
  const std::string output(result.ptr(), result.length());
  EXPECT_EQ(0U, output.find("你好{1}/你好{1}/"));
  EXPECT_NE(std::string::npos, output.find("true"));
}
} // namespace
