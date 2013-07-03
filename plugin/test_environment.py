import sys
import contextlib
import clang.cindex
import re

clang_path = "/Users/mkl/projects/llvm/build/Release+Asserts/lib"
sys.argv = [clang_path]

import libclang

if not libclang.clang.cindex.Config.library_path:
    libclang.clang.cindex.Config.set_library_path(clang_path)


@contextlib.contextmanager
def translation_unit_for(contents):
    yield clang.cindex.TranslationUnit.from_source(
        "some_file.cpp",
        "",
        [("some_file.cpp", contents)])


def mark_unexpected_ranges(source, expected_ranges, actual_ranges):
    """Doesn't handle overlapping ranges"""
    missing_ranges = set(actual_ranges) - set(expected_ranges)

    result = ""
    current_position = 0
    for range in sorted(missing_ranges):
        result += source[current_position:range[0]]
        result += "<UNEXPECTED|"
        result += source[range[0]: range[1]]
        result += "|UNEXPECTED>"
        current_position = range[1]

    return result


def assert_ranges_equal(test, source, ranges):
    starts = [a.end() for a in re.finditer("/\\*START\\*/", source)]
    ends = [a.start() for a in re.finditer("/\\*END\\*/", source)]

    test.assertEqual(len(starts), len(ends))

    expected_ranges = zip(starts, ends)
    actual_ranges = [(r.start.offset, r.end.offset) for r in ranges]

    print mark_unexpected_ranges(source, expected_ranges, actual_ranges)

    test.assertEqual(set(expected_ranges), set(actual_ranges))
