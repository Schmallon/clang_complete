import sys
#sys.argv = ["/Users/mkl/projects/llvm/build/Release+Asserts/lib"]
sys.argv = ["/Users/mkl/projects/llvm/debug_build/Debug+Asserts/lib"]
import libclang
import unittest


class TestEditor(libclang.Editor):
  def __init__(self):
    self._current_column = -1
    self._current_line = -1
    self._filename = 'invalid filename'
    self._contents = 'invalid contents'

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


class TestClangPlugin(unittest.TestCase):
  def setUp(self):
    self.editor = TestEditor()
    self.clang_plugin = libclang.ClangPlugin(self.editor, 0)

  def assert_jumps_to_definition(self, source_file_name, start_line, start_column, expected_filename, expected_line, expected_column):
    self.editor.open_file("test_sources/" + source_file_name, start_line, start_column)
    self.clang_plugin.jump_to_definition()
    self.assertTrue(self.editor.filename().endswith(expected_filename))
    self.assertEquals(self.editor.current_column(), expected_column)
    self.assertEquals(self.editor.current_line(), expected_line)

  def test_jump_to_definition_in_same_file(self):
    self.assert_jumps_to_definition("a.cpp", 8, 3, "a.cpp", 13, 1)

  def test_jump_to_definition_in_another_file(self):
    self.assert_jumps_to_definition("a.cpp", 9, 3, "b.cpp", 3, 1)

  def test_jump_to_definition_default_to_declaration_if_no_definition_available(self):
    self.assert_jumps_to_definition("a.cpp", 10, 3, "a.cpp", 4, 1)

if __name__ == '__main__':
    unittest.main()
