import sys
sys.argv = ["/Users/mkl/projects/llvm/build/Release+Asserts/lib"]
#sys.argv = ["/Users/mkl/projects/llvm/debug_build/Debug+Asserts/lib"]
import libclang
import unittest


class TestEditor(libclang.Editor):
  def __init__(self):
    self._current_column = -1
    self._current_line = -1
    self._filename = 'invalid filename'
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

  def filename(self):
    return self._filename

  def contents(self):
    return self._contents

  def current_file(self):
    return (self.filename(), self.contents())

  def user_options(self):
    return ""

  def debug_enabled(self):
    return True

  def display_message(self, message):
    print(message)

  def open_file(self, filename, line, column):
    self._filename = filename
    self._contents = open(filename, 'r').read()
    self._current_line = line
    self._current_column = column

  def select_range(self, start, end):
    self._selection = (start, end)

  def selection(self):
    return self._selection


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

  def assert_jumps_to_definition(self, source_file_name, start_line, start_column, expected_filename, expected_line, expected_column):
    self.jump_to_definition(source_file_name, start_line, start_column)
    if not self.editor.filename().endswith(expected_filename):
      self.fail(self.editor.filename() + " does not end with " + expected_filename)
    self.assertEquals(self.editor.current_column(), expected_column)
    self.assertEquals(self.editor.current_line(), expected_line)

  def test_jump_to_definition_in_same_file(self):
    self.assert_jumps_to_definition("test_defined_in_same_file.cpp", 7, 3, "test_defined_in_same_file.cpp", 1, 1)

  def test_jump_to_definition_in_header(self):
    self.assert_jumps_to_definition("test_defined_in_header.cpp", 5, 3, "defined_in_header.h", 1, 1)

  def test_jump_to_definition_in_another_source(self):
    self.assert_jumps_to_definition("test_defined_in_another_source.cpp", 5, 3, "defined_in_source.cpp", 3, 1)

  def test_jump_to_definition_default_to_declaration_if_no_definition_available(self):
    self.assert_jumps_to_definition("test_declared_in_header.cpp", 5, 3, "declared_in_header.h", 1, 1)

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
    self.assertEquals(list(set(referenced_ranges)), [range_from_tuples(self.full_file_name(file_name), (3, 3), (3, 36))])

class TestTranslationUnitParser(unittest.TestCase):
  def test_can_parse(self):
    parser = libclang.SynchronizedTranslationUnitParser(TestEditor())
    file =  ('test.cpp', 'void foo();')
    parser.translation_unit_do(file, lambda translation_unit: translation_unit)

def range_from_tuples(file_name, start, end):
  start_pos = libclang.ExportedPosition(file_name, start[0], start[1])
  end_pos = libclang.ExportedPosition(file_name, end[0], end[1])
  return libclang.ExportedRange(start_pos, end_pos)

class TestFindReferencesToOutsideOfSelectionAction(unittest.TestCase):

  def setUp(self):
    self.editor = TestEditor()
    self.translation_unit_accessor = libclang.TranslationUnitAccessor(self.editor)

  def tearDown(self):
    self.translation_unit_accessor.terminate()

  def translation_unit_do(self, file_name, function):
    return self.translation_unit_accessor.translation_unit_for_file_named_do(file_name, function)

  def create_action(self):
    return libclang.FindReferencesToOutsideOfSelectionAction()

  def test_can_construct_action(self):
    self.create_action()

  def action_do(self, file_name, function):
    def do_it(translation_unit):
      action = self.create_action()
      function(action, translation_unit)
    return self.translation_unit_do(file_name, do_it)

  def test_find_references_to_outside_of_selection(self):
    file_name = "test_sources/test_find_references_to_outside_of_selection.cpp"
    def do_it(action, translation_unit):
      references = action.find_references_to_outside_of_selection(translation_unit, file_name,  ((7, 5), (7, 54)))
      referenced_ranges = map(lambda reference: reference.referenced_range, references)
      self.assertEquals(list(set(referenced_ranges)), [range_from_tuples(file_name, (3, 3), (3, 36))])
    self.action_do(file_name, do_it)

if __name__ == '__main__':
    unittest.main()
