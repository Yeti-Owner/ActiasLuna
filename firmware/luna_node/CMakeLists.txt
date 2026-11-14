cmake_minimum_required(VERSION 3.16)

# One source tree, two roles. Build each role in its own build dir: idf.py -B build_ap -DLUNA_ROLE=AP build
set(LUNA_ROLE "RX" CACHE STRING "Node role: AP or RX")
if(LUNA_ROLE STREQUAL "AP")
    add_compile_definitions(LUNA_ROLE_AP=1)
elseif(LUNA_ROLE STREQUAL "RX")
    add_compile_definitions(LUNA_ROLE_AP=0)
else()
    message(FATAL_ERROR "LUNA_ROLE must be AP or RX, not '${LUNA_ROLE}'")
endif()
message(STATUS "ActiasLuna node role: ${LUNA_ROLE}")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(luna_node)
