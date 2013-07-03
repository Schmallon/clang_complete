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


def assert_ranges_equal(test, source, ranges):
    starts = [a.end() for a in re.finditer("/\\*START\\*/", source)]
    ends = [a.start() for a in re.finditer("/\\*END\\*/", source)]

    test.assertEqual(len(starts), len(ends))

    expected_ranges = zip(starts, ends)
    actual_ranges = [(r.start.offset, r.end.offset) for r in ranges]

    test.assertEqual(set(expected_ranges), set(actual_ranges))
