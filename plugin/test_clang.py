import sys

clang_path = "/Users/mkl/projects/llvm/build/Release+Asserts/lib"
sys.argv = [clang_path]

#sys.argv = ["/Users/mkl/projects/llvm/debug/Debug+Asserts/lib"]
import libclang
import unittest

libclang.clang.cindex.Config.set_library_path(clang_path)

class TestEditor(object):
  def __init__(self):
    self._current_column = -1
    self._current_line = -1
    self._file_name = 'invalid filename'
    self._contents = 'invalid contents'
    self._selection = ((1,1), (1,1))

  def display_diagnostics(self, quickfix_list):
    pass

  def higlight_range(self, start, end):
    pass

  def excluded_directories(self):
    return []

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
    return libclang.ExportedLocation(self.file_name(), self.current_line(), self.current_column())

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

  def open_location(self, location):
    self.open_file(location.file.name, location.line, location.column)

  def select_range(self, start, end):
    self._selection = (start, end)

  def selection(self):
    return range_from_tuples(self.file_name(), self._selection[0], self._selection[1])


class TestClangPlugin(unittest.TestCase):
  def setUp(self):
    self.editor = TestEditor()
    self.clang_plugin = libclang.ClangPlugin(self.editor, 0)

  def tearDown(self):
    self.clang_plugin.terminate()

  def full_file_name(self, file_name):
    return "test_sources/" + file_name

  def open_source_file(self, source_file_name, start_line, start_column):
    self.editor.open_file(self.full_file_name(source_file_name), start_line, start_column)

  def jump_to_definition(self, source_file_name, start_line, start_column):
    self.open_source_file(source_file_name, start_line, start_column)
    self.clang_plugin.jump_to_definition()

  def jump_to_declaration(self, source_file_name, start_line, start_column):
    self.open_source_file(source_file_name, start_line, start_column)
    self.clang_plugin.jump_to_declaration()

  def assert_location(self, expected_file_name, expected_line, expected_column):
    if not self.editor.file_name().endswith(expected_file_name):
      self.fail(self.editor.file_name() + " does not end with " + expected_file_name)
    self.assertEquals(self.editor.current_column(), expected_column)
    self.assertEquals(self.editor.current_line(), expected_line)

  def assert_jumps_to_definition(self, source_file_name, start_line, start_column, expected_file_name, expected_line, expected_column):
    self.jump_to_definition(source_file_name, start_line, start_column)
    self.assert_location(expected_file_name, expected_line, expected_column)

  def assert_jumps_to_declaration(self, source_file_name, start_line, start_column, expected_file_name, expected_line, expected_column):
    self.jump_to_declaration(source_file_name, start_line, start_column)
    self.assert_location(expected_file_name, expected_line, expected_column)

  def test_jump_to_definition_in_same_file(self):
    self.assert_jumps_to_definition("test_defined_in_same_file.cpp", 7, 3, "test_defined_in_same_file.cpp", 1, 1)

  def test_jump_to_definition_in_header(self):
    self.assert_jumps_to_definition("test_defined_in_header.cpp", 5, 3, "defined_in_header.h", 1, 1)

  def test_jump_to_definition_in_another_source(self):
    self.assert_jumps_to_definition("test_defined_in_another_source.cpp", 5, 3, "defined_in_source.cpp", 3, 1)

  def test_jump_to_definition_default_to_declaration_if_no_definition_available(self):
    self.assert_jumps_to_definition("test_declared_in_header.cpp", 5, 3, "declared_in_header.h", 1, 1)

  def test_jump_to_declaration(self):
    self.assert_jumps_to_declaration("test_declared_in_header.cpp", 5, 3, "declared_in_header.h", 1, 1)

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
    self.editor.select_range((7,5),(7,54))
    references = self.clang_plugin.find_references_to_outside_of_selection()
    referenced_ranges = map(lambda reference: reference.referenced_range, references)
    self.assertEquals(list(set(referenced_ranges)), [range_from_tuples(self.full_file_name(file_name), (3, 7), (3, 36))])

class TestTranslationUnitParser(unittest.TestCase):
  def test_can_parse(self):
    parser = libclang.SynchronizedTranslationUnitParser(TestEditor())
    file =  ('test.cpp', 'void foo();')
    parser.translation_unit_do(file, lambda translation_unit: translation_unit)

def range_from_tuples(file_name, start, end):
  start_pos = libclang.ExportedLocation(file_name, start[0], start[1])
  end_pos = libclang.ExportedLocation(file_name, end[0], end[1])
  return libclang.ExportedRange(start_pos, end_pos)


#We really shouldn't solve this via inheritance.
class TestCaseWithTranslationUnitAccessor(unittest.TestCase):
  def setUp(self):
    self.editor = TestEditor()
    self.translation_unit_accessor = libclang.TranslationUnitAccessor(self.editor)

  def tearDown(self):
    self.translation_unit_accessor.terminate()

  def translation_unit_do(self, file_name, function):
    return self.translation_unit_accessor.translation_unit_for_file_named_do(file_name, function)

class TestFindReferencesToOutsideOfSelectionAction(TestCaseWithTranslationUnitAccessor):
  def create_action(self):
    return libclang.FindReferencesToOutsideOfSelectionAction()

  def action_do(self, file_name, function):
    def do_it(translation_unit):
      action = self.create_action()
      function(action, translation_unit)
    return self.translation_unit_do(file_name, do_it)

  def assert_returns_ranges(self, file_name, given_range, expected_referenced_ranges, expected_referencing_ranges):
    def do_it(action, translation_unit):
      references = action.find_references_to_outside_of_selection(translation_unit, given_range)

      referenced_ranges = map(lambda reference: reference.referenced_range, references)
      self.assertEquals(list(set(referenced_ranges)), expected_referenced_ranges)

      referencing_ranges = map(lambda reference: reference.referencing_range, references)
      self.assertEquals(list(set(referencing_ranges)), expected_referencing_ranges)
    self.action_do(file_name, do_it)

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
      action = libclang.FindParametersPassedByNonConstReferenceAction(self.editor)
      ranges = action.find_ranges(translation_unit)
      self.assertEquals(list(set(ranges)), expected_ranges)
    self.translation_unit_do(file_name, do_it)

  def test_find_parameter_passed_by_nonconst_reference(self):
    file_name = "test_sources/test_find_parameters_passed_by_reference.cpp"
    self.assert_returns_ranges(file_name, [range_from_tuples(file_name, (12, 17), (12, 29))])

  #def test_find_parameter_passed_by_nonconst_reference_to_constructor(self):
    #file_name = "test_sources/test_find_parameters_passed_by_reference_to_constructor.cpp"
    #self.assert_returns_ranges(file_name, [range_from_tuples(file_name, (14, 11), (14, 30))])

  def test_find_parameter_passed_by_nonconst_reference_to_stream_operators(self):
    file_name = "test_sources/test_find_parameters_passed_by_reference_to_stream_operators.cpp"
    self.assert_returns_ranges(file_name, [range_from_tuples(file_name, (15, 3), (15, 6))])

class TestActions(TestCaseWithTranslationUnitAccessor):

  def assert_returns_ranges(self, action, file_name, expected_ranges):
    actual_ranges = []
    def do_it(translation_unit):
      actual_ranges.extend(list(set(action.find_ranges(translation_unit))))
    self.translation_unit_do(file_name, do_it)
    self.assertEquals(set(actual_ranges), set(expected_ranges))

  def test_find_virtual_method_calls(self):
    file_name = "test_sources/test_find_virtual_method_calls.cpp"
    self.assert_returns_ranges(
        libclang.FindVirtualMethodCallsAction(),
        file_name,
        [range_from_tuples(file_name, (13, 3), (13, 23))])

  def test_find_omitted_default_arguments(self):
    file_name = "test_sources/test_find_omitted_default_arguments.cpp"
    self.assert_returns_ranges(
      libclang.FindOmittedDefaultArgumentsAction(),
      file_name,
      [range_from_tuples(file_name, (5, 3), (5, 37))])

  def test_find_virtual_method_declarations(self):
    self.maxDiff = None
    file_name = "test_sources/test_find_virtual_method_declarations.cpp"
    self.assert_returns_ranges(
      libclang.FindVirtualMethodDeclarationsAction(),
      file_name,
      [range_from_tuples(file_name, (5, 16), (5, 30)), range_from_tuples(file_name, (8, 11), (8, 25))])

  def test_find_static_method_declarations(self):
    self.maxDiff = None
    file_name = "test_sources/test_find_static_method_declarations.cpp"
    self.assert_returns_ranges(
      libclang.FindStaticMethodDeclarationsAction(),
      file_name,
      [range_from_tuples(file_name, (5, 15), (5, 28)), range_from_tuples(file_name, (8, 11), (8, 24))])

  def test_find_member_references(self):
    self.maxDiff = None
    file_name = "test_sources/test_find_member_references.cpp"
    self.assert_returns_ranges(
      libclang.FindMemberReferencesAction(),
      file_name,
      [range_from_tuples(file_name, (19, 12), (19, 35)),
       range_from_tuples(file_name, (24, 12), (24, 26)),
       range_from_tuples(file_name, (29, 12), (29, 18)),
       range_from_tuples(file_name, (34, 5), (34, 12))])

  #def test_find_private_method_declarations(self):
    #self.maxDiff = None
    #file_name = "test_sources/test_find_private_public_method_declarations.cpp"
    #self.assert_returns_ranges(
      #libclang.FindPrivateMethodDeclarationsAction(),
      #file_name,
      #[range_from_tuples(file_name, (4, 8), (4, 22)),
       #range_from_tuples(file_name, (13, 6), (13, 25))])

class TestGetIdentifierRange(TestCaseWithTranslationUnitAccessor):

  def assert_gets_range(self, file_name, location, expected_range):
    def do_it(translation_unit):
      clang_location = libclang.ExportedLocation(file_name, location[0], location[1]).clang_location(translation_unit)

      cursor = libclang.clang.cindex.Cursor.from_location(translation_unit, clang_location)
      identifier_range = libclang.ExportedRange.from_clang_range(cursor.identifier_range)
      expected_range_real_range = range_from_tuples(file_name, expected_range[0], expected_range[1])
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

if __name__ == '__main__':
    unittest.main()
