cmake_minimum_required(VERSION 3.16)
project(SeekdbAICoreTests LANGUAGES CXX)

# Standalone coverage for SQL-owned algorithms and the local HTTP fixture.
# Provider/ObAIFuncClient tests require the full seekdb runtime and are excluded.
set(GTEST_SOURCE_DIR "" CACHE PATH "GoogleTest source checkout")
option(AI_TEST_SANITIZERS "Enable address and undefined-behavior sanitizers" ON)
if(NOT EXISTS "${GTEST_SOURCE_DIR}/CMakeLists.txt")
  message(FATAL_ERROR "Set GTEST_SOURCE_DIR to a GoogleTest source checkout")
endif()
set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
add_subdirectory("${GTEST_SOURCE_DIR}" "${CMAKE_BINARY_DIR}/googletest" EXCLUDE_FROM_ALL)

get_filename_component(SEEKDB_SOURCE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../../.." ABSOLUTE)
add_executable(ai_function_core_tests
  ob_expr_ai_prompt_test.cpp
  test_ai_response_index_map.cpp
)
target_compile_features(ai_function_core_tests PRIVATE cxx_std_17)
target_include_directories(ai_function_core_tests PRIVATE "${SEEKDB_SOURCE_ROOT}/src")
target_link_libraries(ai_function_core_tests PRIVATE gtest_main)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(ai_function_core_tests PRIVATE -Wall -Wextra -Werror)
  if(AI_TEST_SANITIZERS)
    target_compile_options(ai_function_core_tests PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(ai_function_core_tests PRIVATE -fsanitize=address,undefined)
  endif()
endif()

find_package(Python3 COMPONENTS Interpreter REQUIRED)
enable_testing()
add_test(NAME ai_function_core COMMAND ai_function_core_tests)
add_test(NAME ai_mock_protocol COMMAND "${Python3_EXECUTABLE}" -B -m unittest discover
  -s "${CMAKE_CURRENT_LIST_DIR}" -p test_ai_mock_service.py -v)

add_test(NAME ai_sql_selection COMMAND "${Python3_EXECUTABLE}" -B -m unittest discover
  -s "${CMAKE_CURRENT_LIST_DIR}" -p test_ai_sql_runner.py -v)
