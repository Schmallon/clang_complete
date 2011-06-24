import sys
sys.argv = ["/Users/mkl/projects/llvm/build/Release+Asserts/lib"]
import libclang
import unittest


class TestEditor(libclang.Editor):
  def __init__(self):
    self._current_column = 16
    self._current_line = 3

  def current_line(self):
    return self._current_line

  def current_column(self):
    return self._current_column

  def filename(self):
    return 'foo.cpp'

  def contents(self):
    return """           //1
  void foo() {}          //2
  void bar() {foo();}    //3"""

  def current_file(self):
    return (self.filename(), self.contents())

  def user_options(self):
    return ""

  def debug_enabled(self):
    return False

  def display_message(self, message):
    pass

  def open_file(self, filename, line, column):
    self._current_line = line
    self._current_column = column
    "Changing files not yet supported by test editor"
    assert filename == self.filename()


class TestClangPlugin(unittest.TestCase):
  def setUp(self):
    self.editor = TestEditor()
    self.clang_plugin = libclang.ClangPlugin(self.editor, 0)


  def test_jump_to_definition(self):
    self.clang_plugin.jump_to_definition()
    expected_line = 2
    expected_column = 3
    self.assertEquals(self.editor.current_column(), expected_column)
    self.assertEquals(self.editor.current_line(), expected_line)

if __name__ == '__main__':
    unittest.main()
