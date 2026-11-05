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

#include "sql/engine/expr/ob_expr_ai/ai_prompt_template.h"

#include <gtest/gtest.h>
#include <iostream>
#include <string>
#include <vector>

using oceanbase::common::visit_ai_prompt_template;

namespace
{
int checks = 0;
void check(const std::string &text, const std::vector<std::string> &args,
           bool expected_valid, const std::string &expected = "")
{
  ++checks;
  std::string output;
  bool valid = visit_ai_prompt_template(text.data(), text.size(), args.size(),
      [&](const char *literal, int64_t length, int64_t index) {
        if (index < 0) { output.append(literal, length); }
        else { output.append(args.at(index)); }
        return true;
      });
  if (valid != expected_valid || (valid && output != expected)) {
    std::cerr << "FAIL: " << text << " -> " << output << '\n';
    ADD_FAILURE() << "unexpected template expansion";
  }
}
}

TEST(AIPromptTemplate, ExpansionAndInvalidPlaceholders)
{
  check("{0}/{0}/{1}", {std::string(100000, 'x'), "end"}, true,
        std::string(100000, 'x') + "/" + std::string(100000, 'x') + "/end");
  check("你好，{0} 🌊", {"世界"}, true, "你好，世界 🌊");
  check("{{{0}}}", {"{1}"}, true, "{{1}}");
  check("{{0}}", {}, true, "{0}");
  check("{1}{0}{1}", {"a", "b"}, true, "bab");
  check("{00}", {"a"}, true, "a");
  check("literal", {}, true, "literal");
  check("", {}, true, "");
  check(std::string("a\0{0}", 5), {std::string("b\0c", 3)}, true, std::string("a\0b\0c", 5));
  check("{0}", {}, false);
  check("{1}", {"a"}, false);
  check("{-1}", {"a"}, false);
  check("{x}", {"a"}, false);
  check("{}", {"a"}, false);
  check("{0", {"a"}, false);
  check("}", {}, false);
  check("{9223372036854775808}", {"a"}, false);
  check("{999999999999999999999999999999999999}", {"a"}, false);
  bool called = false;
  bool valid = visit_ai_prompt_template("{0}", 3, 1,
      [&](const char *, int64_t, int64_t) { called = true; return false; });
  EXPECT_FALSE(valid);
  EXPECT_TRUE(called);
  ++checks;
}

TEST(AIPromptTemplate, MalformedShortTemplates)
{
  // Exhaust malformed short templates under ASan/UBSan, including nested braces.
  for (int n = 0; n < 4096; ++n) {
    std::string text;
    int digits = n;
    for (int j = 0; j < 6; ++j) { text += "{}0x"[digits & 3]; digits >>= 2; }
    visit_ai_prompt_template(text.data(), text.size(), 1,
        [](const char *, int64_t, int64_t index) { return index < 1; });
  }

}
