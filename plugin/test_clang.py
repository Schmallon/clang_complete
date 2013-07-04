import sys

clang_path = "/Users/mkl/projects/llvm/ninja/lib"
sys.argv = [clang_path]

#sys.argv = ["/Users/mkl/projects/llvm/debug/Debug+Asserts/lib"]
import time
import libclang
import actions
import unittest
import mock
import threading
import translation_unit_access
import common

if libclang.clang.cindex.Config.library_path:
    assert libclang.clang.cindex.Config.library_path == clang_path
else:
    libclang.clang.cindex.Config.set_library_path(clang_path)

def export_ranges(ranges):
    return map(common.ExportedRange.from_clang_range, ranges)

class TestEditor(object):
    def __init__(self):
        self._current_column = -1
        self._current_line = -1
        self._file_name = 'some_file.c'
        self._contents = 'invalid contents'
        self._selection = ((1, 1), (1, 1))
        self._highlights = {}

    def display_diagnostics(self, quickfix_list):
        pass

    def excluded_directories(self):
        return []

    def clear_highlights(self, style):
        self._highlights[style] = []

    def highlight_range(self, range, style):
        highlights = self._highlights[style]
        highlights.append(range)

    def highlights(self):
        return self._highlights

    def sort_algorithm(self):
        return 'priority'

    def abort_requested(self):
        return False

    def current_line(self):
        return self._current_line

    def current_column(self):
        return self._current_column

    def file_name(self):
        return self._file_name

    def current_location(self):
        return common.ExportedLocation(self.file_name(), self.current_line(), self.current_column())

    def contents(self):
        return self._contents

    def current_file(self):
        return (self.file_name(), self.contents())

    def user_options(self):
        return ""

    def debug_enabled(self):
        return True

    def display_message(self, message):
        print(message)

    def open_file(self, file_name, line, column):
        self._file_name = file_name
        self._contents = open(file_name, 'r').read()
        self._current_line = line
        self._current_column = column

    def set_content(self, content):
        self._contents = content

    def open_location(self, location):
        self.open_file(location.file.name, location.line, location.column)

    def select_range(self, start, end):
        self._selection = (start, end)

    def selection(self):
        return range_from_tuples(self.file_name(), self._selection[0], self._selection[1])


class TestClangPlugin(unittest.TestCase):
    def setUp(self):
        self.editor = TestEditor()
        self.clang_plugin = libclang.ClangPlugin(self.editor, 0, "")

    def tearDown(self):
        self.clang_plugin.terminate()

    def full_file_name(self, file_name):
        return "test_sources/" + file_name

    def open_source_file(self, source_file_name, start_line, start_column):
        self.editor.open_file(self.full_file_name(
            source_file_name), start_line, start_column)

    def jump_to_definition(self, source_file_name, start_line, start_column):
        self.open_source_file(source_file_name, start_line, start_column)
        self.clang_plugin.jump_to_definition()

    def jump_to_declaration(self, source_file_name, start_line, start_column):
        self.open_source_file(source_file_name, start_line, start_column)
        self.clang_plugin.jump_to_declaration()

    def assert_location(self, expected_file_name, expected_line, expected_column):
        if not self.editor.file_name().endswith(expected_file_name):
            self.fail(self.editor.file_name(
            ) + " does not end with " + expected_file_name)
        self.assertEquals(self.editor.current_column(), expected_column)
        self.assertEquals(self.editor.current_line(), expected_line)

    def assert_jumps_to_definition(self, source_file_name, start_line, start_column, expected_file_name, expected_line, expected_column):
        self.jump_to_definition(source_file_name, start_line, start_column)
        self.assert_location(
            expected_file_name, expected_line, expected_column)

    def assert_jumps_to_declaration(self, source_file_name, start_line, start_column, expected_file_name, expected_line, expected_column):
        self.jump_to_declaration(source_file_name, start_line, start_column)
        self.assert_location(
            expected_file_name, expected_line, expected_column)

    def test_jump_to_definition_in_same_file(self):
        self.assert_jumps_to_definition("test_defined_in_same_file.cpp",
                                        7, 3, "test_defined_in_same_file.cpp", 1, 1)

    def test_jump_to_definition_in_header(self):
        self.assert_jumps_to_definition("test_defined_in_header.cpp",
                                        5, 3, "defined_in_header.h", 1, 1)

    def test_jump_to_definition_in_another_source(self):
        self.assert_jumps_to_definition("test_defined_in_another_source.cpp",
                                        5, 3, "defined_in_source.cpp", 3, 1)

    def test_jump_to_definition_default_to_declaration_if_no_definition_available(self):
        self.assert_jumps_to_definition("test_declared_in_header.cpp",
                                        5, 3, "declared_in_header.h", 1, 1)

    def test_jump_to_declaration(self):
        self.assert_jumps_to_declaration("test_declared_in_header.cpp",
                                         5, 3, "declared_in_header.h", 1, 1)

    def test_expression_in_macro(self):
        # For now ensure that we don't crash
        self.jump_to_definition("test_reference_in_macro.cpp", 9, 9)
        #self.assert_jumps_to_definition("test_reference_in_macro.cpp", 9, 9, "test_reference_in_macro.h", 3, 1)

    def test_completion_triggers(self):
        # For now ensure that we don't crash
        self.open_source_file("test_incomplete.cpp", 7, 7)
        self.clang_plugin.get_current_completions("")

    def test_defined_in_another_source_declaration_starting_with_other_reference(self):
        self.assert_jumps_to_definition(
            "test_defined_in_another_source_declaration_starting_with_other_reference.cpp", 5, 3,
            "defined_in_another_source_declaration_starting_with_other_reference.cpp", 3, 1)

    def test_jump_from_source_included_by_header(self):
        self.assert_jumps_to_definition(
            "defined_in_source_included_by_headerX.cpp", 3, 3,
            "defined_in_source_included_by_header.cpp", 3, 1)

    def test_find_references_to_outside_of_selection(self):
        file_name = "test_find_references_to_outside_of_selection.cpp"
        self.open_source_file(file_name, 1, 1)
        self.editor.select_range((7, 5), (7, 54))
        references = self.clang_plugin.find_references_to_outside_of_selection(
        )
        referenced_ranges = export_ranges(map(
            lambda reference: reference.referenced_range, references))
        self.assertEquals(list(set(referenced_ranges)), [range_from_tuples(
            self.full_file_name(file_name), (3, 7), (3, 36))])

    def wait_until_parsed(self):
        time.sleep(0.2)

    def test_diagnostics_are_hidden_when_fixed(self):
        self.editor.set_content("foo")
        self.clang_plugin.file_changed()
        self.wait_until_parsed()

        self.clang_plugin.tick()
        self.assertTrue(self.editor.highlights()["Diagnostic"])

        self.editor.set_content("void foo(){}")
        self.clang_plugin.file_changed()
        self.wait_until_parsed()

        self.clang_plugin.tick()
        self.assertFalse(self.editor.highlights()["Diagnostic"])

    def test_diagnostics_appear(self):
        self.editor.set_content("void foo(){}")
        self.clang_plugin.file_changed()
        self.wait_until_parsed()

        self.clang_plugin.tick()
        self.assertFalse(self.editor.highlights()["Diagnostic"])

        self.editor.set_content("foo")
        self.clang_plugin.file_changed()
        self.wait_until_parsed()

        self.clang_plugin.tick()
        self.assertTrue(self.editor.highlights()["Diagnostic"])

    def test_diagnostics_are_updated(self):
        self.editor.set_content("foo")
        self.clang_plugin.file_changed()
        self.wait_until_parsed()

        self.clang_plugin.tick()
        self.assertEquals(
                1,
                self.editor.highlights()["Diagnostic"][0].start.line)

        self.editor.set_content("\n\nfoo")
        self.clang_plugin.file_changed()
        self.wait_until_parsed()

        self.clang_plugin.tick()
        self.assertEquals(
                3,
                self.editor.highlights()["Diagnostic"][0].start.line)

    def test_diagnostics_are_updated2(self):

        """Some large number of changes that increases the chance that a
        background thread finishes parsing."""
        num_changes = 10000
        for i in range(1, num_changes):
            self.editor.set_content("\n" * i + "foo")
            self.clang_plugin.file_changed()

        self.wait_until_parsed()
        print "foo"
        self.wait_until_parsed()

        self.clang_plugin.tick()
        self.clang_plugin.tick()

        self.assertEquals(
                num_changes,
                self.editor.highlights()["Diagnostic"][0].start.line)


class TestTranslationUnitParser(unittest.TestCase):
    def test_files_changed_while_parsing_should_not_be_up_to_date(self):

        index = mock.MagicMock(spec=[])

        continue_parsing = threading.Event()
        is_in_parser = threading.Event()

        def wait_for_event(*args):
            is_in_parser.set()
            continue_parsing.wait(1)

        index.parse = mock.MagicMock(wraps=wait_for_event)
        parser = translation_unit_access.SynchronizedTranslationUnitParser(
                index, TestEditor())

        def parse():
            parser.translation_unit_do(
                    "foo.cpp",
                    lambda: "void foo();",
                    lambda tu: tu)

        thread = threading.Thread(target=parse)

        thread.start()
        is_in_parser.wait()
        parser.clear_caches()
        continue_parsing.set()
        thread.join()

        self.assertFalse(parser.is_up_to_date("foo.cpp"))


def range_from_tuples(file_name, start, end):
    start_pos = common.ExportedLocation(file_name, start[0], start[1])
    end_pos = common.ExportedLocation(file_name, end[0], end[1])
    return common.ExportedRange(start_pos, end_pos)


#We really shouldn't solve this via inheritance.
class TestCaseWithTranslationUnitAccessor(unittest.TestCase):
    def setUp(self):
        self.editor = TestEditor()
        self.translation_unit_accessor = libclang.TranslationUnitAccessor(
            self.editor)

    def tearDown(self):
        self.translation_unit_accessor.terminate()

    def translation_unit_do(self, file_name, function):
        return self.translation_unit_accessor.translation_unit_for_file_named_do(file_name, function)


class TestFindReferencesToOutsideOfSelection(TestCaseWithTranslationUnitAccessor):
    def assert_returns_ranges(self, file_name, given_range, expected_referenced_ranges, expected_referencing_ranges):
        def do_it(translation_unit):
            references = actions.find_references_to_outside_of_selection(
                translation_unit, given_range)

            referenced_ranges = export_ranges(map(lambda reference:
                                    reference.referenced_range, references))
            self.assertEquals(list(set(
                referenced_ranges)), expected_referenced_ranges)

            referencing_ranges = export_ranges(map(lambda reference:
                                     reference.referencing_range, references))
            self.assertEquals(list(set(
                referencing_ranges)), expected_referencing_ranges)
        self.translation_unit_do(file_name, do_it)

    def test_find_references_to_outside_of_selection2(self):
        file_name = "test_sources/test_find_references_to_outside_of_selection.cpp"
        self.assert_returns_ranges(
            file_name,
            range_from_tuples(file_name, (7, 5), (7, 54)),
            [range_from_tuples(file_name, (3, 7), (3, 36))],
            [range_from_tuples(file_name, (7, 25), (7, 50))])

    def test_find_references_to_variable_defined_on_same_level(self):
        file_name = "test_sources/test_find_references_to_variable_defined_on_same_level.cpp"
        self.assert_returns_ranges(
            file_name,
            range_from_tuples(file_name, (5, 3), (5, 27)),
            [range_from_tuples(file_name, (3, 7), (3, 28))],
            [range_from_tuples(file_name, (5, 3), (5, 24))])

    def test_find_references_with_selecting_extra_whitespace_works(self):
        file_name = "test_sources/test_find_references_to_variable_defined_on_same_level.cpp"
        self.assert_returns_ranges(
            file_name,
            range_from_tuples(file_name, (5, 1), (5, 27)),
            [range_from_tuples(file_name, (3, 7), (3, 28))],
            [range_from_tuples(file_name, (5, 3), (5, 24))])


class TestFindParametersPassedByNonConstReference(TestCaseWithTranslationUnitAccessor):
    def assert_returns_ranges(self, file_name, expected_ranges):
        def do_it(translation_unit):
            action = actions.make_find_parameters_passed_by_non_const_reference(
                self.editor)
            ranges = export_ranges(action(translation_unit))
            self.assertEquals(list(set(ranges)), expected_ranges)
        self.translation_unit_do(file_name, do_it)

    def test_find_parameter_passed_by_nonconst_reference(self):
        file_name = "test_sources/test_find_parameters_passed_by_reference.cpp"
        self.assert_returns_ranges(file_name, [range_from_tuples(
            file_name, (12, 17), (12, 29))])

    #def test_find_parameter_passed_by_nonconst_reference_to_constructor(self):
        #file_name = "test_sources/test_find_parameters_passed_by_reference_to_constructor.cpp"
        #self.assert_returns_ranges(file_name, [range_from_tuples(file_name, (14, 11), (14, 30))])

    def test_find_parameter_passed_by_nonconst_reference_to_stream_operators(self):
        file_name = "test_sources/test_find_parameters_passed_by_reference_to_stream_operators.cpp"
        self.assert_returns_ranges(file_name, [range_from_tuples(
            file_name, (15, 3), (15, 6))])


class TestGetIdentifierRange(TestCaseWithTranslationUnitAccessor):

    def assert_gets_range(self, file_name, location, expected_range):
        def do_it(translation_unit):
            clang_location = common.ExportedLocation(file_name, location[
                0], location[1]).clang_location(translation_unit)

            cursor = libclang.clang.cindex.Cursor.from_location(
                translation_unit, clang_location)
            identifier_range = common.ExportedRange.from_clang_range(actions.get_identifier_range(cursor))
            expected_range_real_range = range_from_tuples(
                file_name, expected_range[0], expected_range[1])
            self.assertEquals(identifier_range, expected_range_real_range)

        self.translation_unit_do(file_name, do_it)

    def test_static_method_declaration(self):
        self.assert_gets_range(
            "test_sources/test_get_identifier_range.cpp",
            (4, 12),
            ((4, 15), (4, 28)))

    def test_virtual_method_declaration(self):
        self.assert_gets_range(
            "test_sources/test_get_identifier_range.cpp",
            (5, 3),
            ((5, 16), (5, 30)))

    def test_virtual_method_definition(self):
        self.assert_gets_range(
            "test_sources/test_get_identifier_range.cpp",
            (18, 1),
            ((18, 11), (18, 25)))


class TestTranslationUnitAccessor(unittest.TestCase):
    """Would be nice to decompose this into MustBeRunMixin and
    TestTranslationUnitAccessorMixin. Need more python-fu for that."""

    def setUp(self):
        self.must_be_run = {}

        self.editor = TestEditor()
        self.translation_unit_accessor = libclang.TranslationUnitAccessor(
            self.editor)

    def tearDown(self):
        self.translation_unit_accessor.terminate()
        for marker, was_run in self.must_be_run.items():
            self.assertTrue(was_run)

    def assert_was_run(self, f):
        class Marker(object):
            pass
        marker = Marker()
        self.must_be_run[marker] = False

        def do_it(*args):
            self.must_be_run[marker] = True
            return f(*args)
        return do_it

    def test_can_parse_current_file(self):
        self.editor.set_content("void foo() {}")
        self.translation_unit_accessor.current_translation_unit_do(
                self.assert_was_run(
                    lambda tu: self.assertEquals(
                        "TRANSLATION_UNIT",
                        tu.cursor.kind.name)))

    def test_can_parse_file(self):
        self.translation_unit_accessor.translation_unit_for_file_named_do(
                "test_sources/simple.cpp",
                self.assert_was_run(
                    lambda tu: self.assertEquals(
                        "TRANSLATION_UNIT",
                        tu.cursor.kind.name)))


class TestIdleTranslationUnitParserThreadDistributor(unittest.TestCase):
    def setUp(self):
        self.editor = TestEditor()
        self.parser = mock.MagicMock(spec=[])
        self.distributor = translation_unit_access.IdleTranslationUnitParserThreadDistributor(
                self.editor, self.parser)

    def tearDown(self):
        self.distributor.terminate()

    def test_changing_file_while_parsing_resuts_in_extra_parse(self):

        is_in_parser = threading.Condition()
        continue_parsing = threading.Condition()

        contents = ""

        def translation_unit_do(file_name, get_contents, enqueue_related_file):
            self.assertEquals(contents, get_contents())
            with is_in_parser:
                is_in_parser.notify()
            with continue_parsing:
                continue_parsing.wait(1)

        self.parser.is_up_to_date = mock.MagicMock(return_value=False)
        self.parser.translation_unit_do = mock.MagicMock(
                wraps=translation_unit_do)

        contents = "void foo();"
        self.distributor.enqueue_file(("file.cpp", contents))

        with is_in_parser:
            is_in_parser.wait(1)

        contents = "invalid"
        self.distributor.enqueue_file(("file.cpp", contents))

        with continue_parsing:
            continue_parsing.notify()

        # We should check whether the wait was successful
        with is_in_parser:
            is_in_parser.wait(1)

        with continue_parsing:
            continue_parsing.notify()


class TestIdleTranslationUnitParserThread(unittest.TestCase):
    def test_parsing_enqueues_related_files(self):
        pass

        #parser = mock.MagicMock(spec=[])
        #thread = IdleTranslationUnitParserThread(None, parser, )

# TODO:
# Test that IdleTranslationUnitParserThread parses similarly named files.
# Change code to enqueue related files *before* parsing the target file
# Threading by inheritance? Who had this idea?

if __name__ == '__main__':
    unittest.main()
