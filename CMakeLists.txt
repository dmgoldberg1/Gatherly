cmake_minimum_required(VERSION 3.22)

project(Gatherly LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -O2 -pipe")

option(GATHERLY_REQUIRE_DROGON "Fail configure if Drogon is not available" OFF)
option(GATHERLY_REQUIRE_POSTGRES "Fail configure if libpqxx is not available" OFF)

include(FetchContent)
find_package(GTest QUIET)
if (NOT GTest_FOUND)
  FetchContent_Declare(
    googletest
    URL https://github.com/google/googletest/archive/03597a01ee50ed33e9dfd640b249b4be3799d395.zip
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
  )
  set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
  FetchContent_MakeAvailable(googletest)
endif()

find_package(Drogon CONFIG QUIET)
find_path(JSONCPP_INCLUDE_DIR NAMES json/json.h PATH_SUFFIXES jsoncpp)
find_library(JSONCPP_LIBRARY NAMES jsoncpp)
set(GATHERLY_DROGON_TARGET "")
if (TARGET Drogon::Drogon)
  set(GATHERLY_DROGON_TARGET Drogon::Drogon)
elseif (TARGET Drogon::drogon)
  set(GATHERLY_DROGON_TARGET Drogon::drogon)
elseif (TARGET drogon)
  set(GATHERLY_DROGON_TARGET drogon)
endif()

find_package(PkgConfig QUIET)
if (PkgConfig_FOUND)
  if (GATHERLY_DROGON_TARGET STREQUAL "")
    pkg_check_modules(DROGON_PKG QUIET IMPORTED_TARGET drogon)
    if (DROGON_PKG_FOUND)
      set(GATHERLY_DROGON_TARGET PkgConfig::DROGON_PKG)
    endif()
  endif()
  pkg_check_modules(PQXX QUIET IMPORTED_TARGET libpqxx)
endif()

if (GATHERLY_DROGON_TARGET STREQUAL "")
  if (DROGON_LIBRARIES OR Drogon_LIBRARIES)
    add_library(gatherly_drogon_config_vars INTERFACE)
    target_include_directories(gatherly_drogon_config_vars INTERFACE ${DROGON_INCLUDE_DIRS} ${Drogon_INCLUDE_DIRS})
    target_link_libraries(gatherly_drogon_config_vars INTERFACE ${DROGON_LIBRARIES} ${Drogon_LIBRARIES})
    set(GATHERLY_DROGON_TARGET gatherly_drogon_config_vars)
  endif()
endif()

if (GATHERLY_DROGON_TARGET STREQUAL "")
  find_path(DROGON_INCLUDE_DIR NAMES drogon/drogon.h)
  find_library(DROGON_LIBRARY NAMES drogon)
  find_library(TRANTOR_LIBRARY NAMES trantor)
  find_library(UUID_LIBRARY NAMES uuid)
  find_package(Threads REQUIRED)
  if (DROGON_INCLUDE_DIR AND DROGON_LIBRARY)
    add_library(gatherly_drogon_system INTERFACE)
    target_include_directories(gatherly_drogon_system INTERFACE ${DROGON_INCLUDE_DIR})
    target_link_libraries(gatherly_drogon_system INTERFACE ${DROGON_LIBRARY} ${TRANTOR_LIBRARY} ${JSONCPP_LIBRARY} ${UUID_LIBRARY} Threads::Threads)
    set(GATHERLY_DROGON_TARGET gatherly_drogon_system)
  endif()
endif()

if (GATHERLY_REQUIRE_DROGON AND GATHERLY_DROGON_TARGET STREQUAL "")
  message(FATAL_ERROR "Drogon is required but no CMake target, pkg-config module or system library was found")
endif()

if (GATHERLY_REQUIRE_POSTGRES AND NOT PQXX_FOUND)
  message(FATAL_ERROR "libpqxx is required but was not found")
endif()

enable_testing()

add_library(
  gatherly_core
  src/models/models.cpp
  src/services/gatherly_service.cpp
  src/utils/json.cpp
  src/utils/auth.cpp
  src/controllers/api_controller.cpp
)
target_include_directories(gatherly_core PUBLIC src)


if (NOT GATHERLY_DROGON_TARGET STREQUAL "")
  add_executable(
    gatherly_drogon
    src/drogon_main.cpp
  )
  target_link_libraries(gatherly_drogon PRIVATE gatherly_core ${GATHERLY_DROGON_TARGET})
  if (JSONCPP_INCLUDE_DIR)
    target_include_directories(gatherly_drogon PRIVATE ${JSONCPP_INCLUDE_DIR})
  endif()
  if (JSONCPP_LIBRARY)
    target_link_libraries(gatherly_drogon PRIVATE ${JSONCPP_LIBRARY})
  endif()
  if (PQXX_FOUND)
    target_sources(gatherly_drogon PRIVATE src/db/postgres_store.cpp)
    target_link_libraries(gatherly_drogon PRIVATE PkgConfig::PQXX)
    target_compile_definitions(gatherly_drogon PRIVATE GATHERLY_HAS_POSTGRES_STORE)
  endif()
endif()

add_executable(
  gatherly_tests
  tests/domain_tests.cpp
)
target_link_libraries(gatherly_tests PRIVATE gatherly_core GTest::gtest_main)

include(GoogleTest)
gtest_discover_tests(gatherly_tests)
