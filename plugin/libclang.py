from clang.cindex import *
import time
import re
import threading
import os
import sys
import Levenshtein

"""
Ideas:

  - Implement completion and diagnostics for emacs
  For that to work I should first check which parts that are currently
  implemented in vimscript are actually vim specific and vice versa.

  - Allow generic configuration for both vim and emacs

  - Integrate with tags
  For the time that we do not have an index that contains all of a projects
  files, we could make use of tags (as extracted by ctags) to determine which
  files to parse next when looking for definitions.

  - Add a "jump to declaration"
  Often we want to jump to a declaration (e.g. declarations usually a coupled
  with comments). If there was a way to find all declarations referenced by a
  cursor, we could use some heuristics to find the declaration that we want to
  display.

  - Code cleanup
   - Mark all private methods/fields as such
   - There seems to be some confusion between returning NULL or nullCursor
     - get_semantic_parent returns nullCursor
     - get_definition returns NULL
   - Declaration/Definition finding share lots of code.
    - both implementations consist out of two classes
     - find the cursor
     - jump to the cursor
  - When jumping to definitions, print a debug message that explains how the definition
    was found (through which file)
  - Allow jumping through pimpls
  - When opening a new file, right away get possible translation units
   - keep a set of translation unit (name -> translation unit)
    - ensure that accessing this set always uses the most current version of the file
   - the current file
   - an alternate file (.h -> .cpp)
   - *not required* referencing translation unit, as we already were there

  - Integrate Jump-To-Definition with tags-based searching
   - Allow finding definitions of commented code
   - Macros
"""

def print_cursor_with_children(self, cursor, n = 0):
  sys.stdout.write(n * " ")
  print(str(cursor.kind.name))
  for child in cursor.get_children():
    print_cursor_with_children(child, n + 1)

class Editor(object):
  """
  These aren't really properties of an editor.
  """
  def get_current_location_in_translation_unit(self, translation_unit):
    file = translation_unit.getFile(self.filename())
    if not file:
      self.display_message("""Could not find the file at current position in the current
      translation unit""")
      return None
    return translation_unit.getLocation(file, self.current_line(), self.current_column())

  def get_current_cursor_in_translation_unit(self, translation_unit):
    location = self.get_current_location_in_translation_unit(translation_unit)
    return translation_unit.getCursor(location)

  def jump_to_cursor(self, cursor):
    location = cursor.extent.start
    self.open_file(location.file.name, location.line, location.column)

class VimInterface(Editor):

  def __init__(self):
    import vim
    self._vim = vim

  # Get a tuple (filename, filecontent) for the file opened in the current
  # vim buffer. The filecontent contains the unsafed buffer content.
  def current_file(self):
    file = "\n".join(self._vim.eval("getline(1, '$')"))
    return (self.filename(), file)

  def _get_variable(self, variable_name, default_value = ""):
    try:
      return self._vim.eval(variable_name)
    except vim.error:
      return default_value

  def _split_options(self, options):
    optsList = []
    opt = ""
    quoted = False

    for char in options:
      if char == ' ' and not quoted:
        if opt != "":
          optsList += [opt]
          opt = ""
        continue
      elif char == '"':
        quoted = not quoted
      opt += char

    if opt != "":
      optsList += [opt]
    return optsList

  def user_options(self):
    user_options_global = self._split_options(self._get_variable("g:clang_user_options"))
    user_options_local = self._split_options(self._get_variable("b:clang_user_options"))
    return user_options_global + user_options_local

  def excluded_directories(self):
    return self._split_options(self._get_variable("g:clang_excluded_directories"))

  def filename(self):
    return self._vim.current.buffer.name

  def open_file(self, filename, line, column):
    self._vim.command("e +" + str(line) + " " + filename)

  def debug_enabled(self):
    return int(self._vim.eval("g:clang_debug")) == 1

  def current_line(self):
    return int(self._vim.eval("line('.')"))

  def current_column(self):
    return int(self._vim.eval("col('.')"))

  def sort_algorithm(self):
    return self._vim.eval("g:clang_sort_algo")

  def abort_requested(self):
    return 0 != int(self._vim.eval('complete_check()'))

  def display_message(self, message):
    self._print_to_file(message)

  def _print_to_file(self, message):
    f = open("log.txt", "a")
    f.write(message + "\n")
    f.close()

  def _display_in_editor(self, message):
    print(message)

  def higlight_range(self, start, end):
    #We could distinguish different severities
    hg_group = 'SpellBad'
    pattern = '/\%' + str(start.line) + 'l' + '\%' \
        + str(start.column) + 'c' + '.*' \
        + '\%' + str(end.column + 1) + 'c/'
    command = "exe 'syntax match' . ' " + hg_group + ' ' + pattern + "'"
    self._vim.command(command)

class EmacsInterface(Editor):

  def __init__(self):
    from Pymacs import lisp as emacs
    self.emacs = emacs

  def current_file(self):
    return (self.filename(), self.emacs.buffer_string())

  def filename(self):
    return self.emacs.buffer_file_name()

  def user_options(self):
    return ""

  def open_file(self, filename, line, column):
    self.emacs.find_file(filename)
    self.emacs.goto_line(line)
    self.emacs.move_to_column(column - 1)

  def debug_enabled(self):
    return False

  def current_line(self):
    return self.emacs.line_number_at_pos()

  def current_column(self):
    return 1 + self.emacs.current_column()

  def display_message(self, message):
    self.emacs.minibuffer_message(message)

class ClangPlugin(object):
  def __init__(self, editor, clang_complete_flags):
    self.editor = editor
    self.translation_unit_accessor = TranslationUnitAccessor(self.editor)
    self.definition_finder = DefinitionFinder(self.editor, self.translation_unit_accessor)
    self.declaration_finder = DeclarationFinder(self.editor, self.translation_unit_accessor)
    self.completer = Completer(self.editor, self.translation_unit_accessor, int(clang_complete_flags))
    self.quick_fix_list_generator = QuickFixListGenerator(self.editor,
        self.translation_unit_accessor)
    self.diagnostics_highlighter = DiagnosticsHighlighter(self.editor)

  def file_changed(self):
    self.editor.display_message("File change was notified, clearing all caches.")
    self.translation_unit_accessor.clear_caches()
    pass

  def file_opened(self):
    self.editor.display_message("Noticed opening of new file, TODO: Ensure we don't try to produce TUs for the same file at the same time")
    # Clang cannot handle multi-threaded access, "Concurrent access to ASTUnit!"
    #self.translation_unit_accessor.start_get_translation_unit_thread(self.editor.current_file())
    #finder = DefinitionFileFinder(self.editor, self.editor.filename())
    #for file_name in finder.definition_files():
    #  self.translation_unit_accessor.start_get_translation_unit_thread(self.translation_unit_accessor.get_file_for_filename(file_name))

  def jump_to_definition(self):
    definition_cursor = self.definition_finder.find_first_definition_cursor()
    if definition_cursor:
      self.editor.jump_to_cursor(definition_cursor)
    else:
      self.editor.display_message("No definition available")

  def jump_to_declaration(self):
    self.declaration_finder.jump_to_declaration()

  def update_current_diagnostics(self):
    self.translation_unit_accessor.clear_caches()
    self.translation_unit_accessor.get_current_translation_unit()

  def get_current_quickfix_list(self):
    return self.quick_fix_list_generator.get_current_quickfix_list()

  def highlight_current_diagnostics(self):
    self.translation_unit_accessor.clear_caches()
    translation_unit = self.translation_unit_accessor.get_current_translation_unit()
    if self.editor.filename() in self.translation_unit_accessor.translation_units:
      self.diagnostics_highlighter.highlight_in_translation_unit(translation_unit)
    else:
      self.editor.display_message("File was not found in current translation unit")

  def get_current_completions(self, base):
    return self.completer.get_current_completions(base)

class NoCurrentTranslationUnit(Exception):
  pass

class TranslationUnitParserThread(threading.Thread):
  def __init__(self, translation_unit_accessor, file):
    threading.Thread.__init__(self)
    self.file = file
    self.result = None
    self.translation_unit_accessor = translation_unit_accessor
    self.editor = translation_unit_accessor.editor
    self.index = translation_unit_accessor.index
    self.translation_units = translation_unit_accessor.translation_units
    self.up_to_date = translation_unit_accessor.up_to_date

  def run(self):
    self.result = self._get_translation_unit()
    self.editor.display_message("Finished getting translation unit")
    self.up_to_date.add(self._filename())

  def _get_translation_unit(self):
    self.editor.display_message("Getting translation unit for " + self._filename())
    if self._filename() in self.translation_units:
      return self._reuse_existing_translation_unit()
    else:
      return self._read_new_translation_unit()

  def _filename(self):
    return self.file[0]

  def _reuse_existing_translation_unit(self):
    tu = self.translation_units[self._filename()]
    if self._filename() not in self.up_to_date:
      self.editor.display_message("Translation unit is possibly not up to date. Reparse is due")
      tu.reparse([self.file])
    return tu

  def _read_new_translation_unit(self):
    flags = TranslationUnit.PrecompiledPreamble | TranslationUnit.CXXPrecompiledPreamble | TranslationUnit.CacheCompletionResults
    args = self.editor.user_options()
    tu = self.index.parse(self._filename(), args, [self.file], flags)

    if tu == None:
      self.editor.display_message("Cannot parse this source file. The following arguments " \
          + "are used for clang: " + " ".join(args))
      return None

    self.translation_units[self._filename()] = tu

    # Reparse to initialize the PCH cache even for auto completion
    # This should be done by index.parse(), however it is not.
    # So we need to reparse ourselves.
    tu.reparse([self.file])
    return tu



class TranslationUnitAccessor(object):

  def __init__(self, editor):
    self.index = Index.create()
    self.translation_units = dict()
    self.up_to_date = set()
    self.editor = editor

  def get_current_translation_unit(self):
    current_file = self.editor.current_file()
    result = self.get_translation_unit(current_file)
    if result:
      return result
    raise NoCurrentTranslationUnit

  def get_file_for_filename(self, filename):
    return (filename, open(filename, 'r').read())

  def get_translation_unit_for_filename(self, filename):
    try:
      file = self.get_file_for_filename(filename)
      return self.get_translation_unit(file)
    except IOError:
      return None

  def clear_caches(self):
    self.up_to_date = set()

  def start_get_translation_unit_thread(self, file):
    thread = TranslationUnitParserThread(self, file)
    thread.start()
    return thread

  def get_translation_unit(self, file):
    thread = self.start_get_translation_unit_thread(file)
    while thread.is_alive():
      thread.join(0.01)
    return thread.result

class DiagnosticsHighlighter(object):

  def __init__(self, editor):
    self.editor = editor

  def _highlight_diagnostic(self, diagnostic):

    if diagnostic.severity not in (diagnostic.Warning, diagnostic.Error):
      return

    self.editor.higlight_range(diagnostic.location, diagnostic.location)

    # Use this wired kind of iterator as the python clang libraries
          # have a bug in the range iterator that stops us to use:
          #
          # | for range in diagnostic.ranges
          #
    for i in range(len(diagnostic.ranges)):
      range_i = diagnostic.ranges[i]
      self.editor.higlight_range(range_i.start, range_i.end)

  def highlight_in_translation_unit(self, translation_unit):
    map(self._highlight_diagnostic, translation_unit.diagnostics)

class QuickFixListGenerator(object):

  def __init__(self, editor, translation_unit_accessor):
    self.editor = editor
    self.translation_unit_accessor = translation_unit_accessor

  def _get_quick_fix(self, diagnostic):
    # Some diagnostics have no file, e.g. "too many errors emitted, stopping now"
    if diagnostic.location.file:
      filename = diagnostic.location.file.name
    else:
      "hack: report errors without files. should nevertheless be in quickfix list"
      self.editor.display_message(diagnostic.spelling)
      filename = ""

    if diagnostic.severity == diagnostic.Warning:
      type = 'W'
    elif diagnostic.severity == diagnostic.Error or diagnostic.severity == diagnostic.Fatal:
      type = 'E'
    else:
      type = 'O'

    return dict({ 'filename' : filename,
      'lnum' : diagnostic.location.line,
      'col' : diagnostic.location.column,
      'text' : diagnostic.spelling,
      'type' : type})

  def _get_quick_fix_list(self, tu):
    return filter (None, map (self._get_quick_fix, tu.diagnostics))

  def get_current_quickfix_list(self):
    if self.editor.filename() in self.translation_unit_accessor.translation_units:
      return self._get_quick_fix_list(self.translation_unit_accessor.translation_units[self.editor.filename()])
    else:
      self.editor.display_message("File was not found in current translation unit")
      return []

class Completer(object):

  def __init__(self, editor, translation_unit_accessor, complete_flags):
    self.editor = editor
    self.translation_unit_accessor = translation_unit_accessor
    self.complete_flags = complete_flags

  def get_current_completion_results(self, line, column):
    self.editor.display_message("Getting completions")
    translation_unit = self.translation_unit_accessor.get_current_translation_unit()
    current_file = self.editor.current_file()
    return translation_unit.codeComplete(self.editor.filename(), line, column, [current_file],
        self.complete_flags)

  def format_results(self, result):
    completion = dict()

    abbr = self.get_abbr(result.string)
    info = filter(lambda x: not x.isKindInformative(), result.string)
    word = filter(lambda x: not x.isKindResultType(), info)
    return_value = filter(lambda x: x.isKindResultType(), info)

    if len(return_value) > 0:
      return_str = return_value[0].spelling + " "
    else:
      return_str = ""

    info = return_str + "".join(map(lambda x: x.spelling, word))
    word = abbr

    completion['word'] = word
    completion['abbr'] = abbr
    completion['menu'] = info
    completion['info'] = info
    completion['dup'] = 1

    # Replace the number that represents a specific kind with a better
    # textual representation.
    completion['kind'] = kinds[result.cursorKind]

    return completion

  def get_current_completions(self, base):

    sort_by_priority = self.editor.sort_algorithm() == 'priority'

    thread = CompleteThread(self.editor,
        self,
        self.editor.current_line(),
        self.editor.current_column())

    thread.start()
    while thread.is_alive():
      thread.join(0.01)
      if self.editor.abort_requested():
        return []
    completionResult = thread.result
    if completionResult is None:
      return []

    regexp = re.compile("^" + base)
    filtered_result = filter(lambda x: regexp.match(self.get_abbr(x.string)),
        completionResult.results)

    get_priority = lambda x: x.string.priority
    get_abbreviation = lambda x: self.get_abbr(x.string).lower()
    if sort_by_priority:
      key = get_priority
    else:
      key = get_abbreviation
    sorted_result = sorted(filtered_result, None, key)
    return map(self.format_results, sorted_result)

  def get_abbr(self, strings):
    tmplst = filter(lambda x: x.isKindTypedText(), strings)
    if len(tmplst) == 0:
      return ""
    else:
      return tmplst[0].spelling


class CompleteThread(threading.Thread):
  lock = threading.Lock()

  def __init__(self, editor, completer, line, column):
    threading.Thread.__init__(self)
    self.editor = editor
    self.completer = completer
    self.line = line
    self.column = column
    self.result = None

  def run(self):
    try:
      CompleteThread.lock.acquire()
      self.result = self.completer.get_current_completion_results(self.line, self.column)
    except Exception:
      self.editor.display_message("Exception occurred in completion thread")
    CompleteThread.lock.release()


class DeclarationFinder(object):

  def __init__(self, editor, translation_unit_accessor):
    self._editor = editor
    self._translation_unit_accessor = translation_unit_accessor

  def _find_declaration_in_translation_unit(self, translation_unit):
    current_location_cursor = self._editor.get_current_cursor_in_translation_unit(translation_unit)
    parent_cursor = current_location_cursor.get_semantic_parent()
    if parent_cursor == Cursor.nullCursor():
      return None
    for child_cursor in parent_cursor.get_children():
      if child_cursor.get_canonical() == current_location_cursor.get_canonical():
        return child_cursor
    return None

  def jump_to_declaration(self):
    declaration_cursor = self._find_declaration_in_translation_unit(self._translation_unit_accessor.get_current_translation_unit())
    if declaration_cursor:
      self._editor.jump_to_cursor(declaration_cursor)
    else:
      self._editor.display_message("No declaration available")

class NoDefinitionFound(Exception):
  pass


def get_definition_or_reference(cursor):
  result = cursor.get_definition()
  if not result and cursor.get_cursor_referenced():
    #self.editor.display_message("Cursor is a reference but we could not find a definition. Jumping to reference.")
    result = cursor.get_cursor_referenced()
  return result


class DefinitionFinder(object):

  def __init__(self, editor, translation_unit_accessor):
    self.editor = editor
    self.translation_unit_accessor = translation_unit_accessor

  class FindDefinitionInTranslationUnit(object):
    def __init__(self, editor, translation_unit, location):
      self.editor = editor
      self.translation_unit = translation_unit
      self.location = location

    def _get_definition_cursor(self):
      cursor = self.translation_unit.getCursor(self.location)
      if cursor.kind.is_unexposed:
        self.editor.display_message("Item at current position is not exposed. Are you in a Macro?")
      return get_definition_or_reference(cursor)

  def _find_definition_in_translation_unit(self, translation_unit, location):
    return self.FindDefinitionInTranslationUnit(
        self.editor,
        translation_unit,
        location)._get_definition_cursor()

  def find_first_definition_cursor(self):
    """
    Tries to find a definition looking in various translation units. Returns the
    first valid one found
    """

    def current_translation_units():
      try:
        return [self.translation_unit_accessor.get_current_translation_unit()]
      except NoCurrentTranslationUnit:
        return []

    def guess_alternate_translation_units(filename):
      def f():
        finder = DefinitionFileFinder(self.editor, filename)
        return filter(lambda x: x is not None,
            map(self.translation_unit_accessor.get_translation_unit_for_filename,
              finder.definition_files()))
      return f

    def definition_or_declaration_cursor_of_current_cursor_in(translation_unit):
      current_location = self.editor.get_current_location_in_translation_unit(translation_unit)
      return self._find_definition_in_translation_unit(translation_unit, current_location)

    def find_corresponding_cursor(cursor, other_translation_unit):
      file = cursor.extent.start.file
      other_file = other_translation_unit.getFile(file.name)
      for offset in range(cursor.extent.start.offset, cursor.extent.end.offset + 1):
        position = other_translation_unit.getLocationForOffset(other_file, offset)
        cursor_at_position = other_translation_unit.getCursor(position)
        if cursor_at_position.get_usr() == cursor.get_usr():
          return cursor_at_position
      return None

    def find_corresponding_cursor_in_alternate_translation_unit(cursor):
      for alternate_translation_unit in guess_alternate_translation_units(cursor.extent.start.file.name)():
        result = find_corresponding_cursor(cursor, alternate_translation_unit)
        if result:
          return result
      return None

    def definition_of_current_cusor_in(translation_unit):
      definition_or_declaration_cursor = definition_or_declaration_cursor_of_current_cursor_in(translation_unit)
      if definition_or_declaration_cursor:
        self.editor.display_message("Found either a definition or a declaration")
        if definition_or_declaration_cursor.is_definition():
          return definition_or_declaration_cursor
        else:
          self.editor.display_message("The first result is not a definition. Searching for definition of first result")
          alternate_result = find_corresponding_cursor_in_alternate_translation_unit(definition_or_declaration_cursor)
          if alternate_result:
            self.editor.display_message("Jumping to alternate result")
            return get_definition_or_reference(alternate_result)
          else:
            self.editor.display_message("Did not find an alternate result. Jumping to initial result.")
            return definition_or_declaration_cursor
      raise NoDefinitionFound

    for get_translation_units in [
        guess_alternate_translation_units(self.editor.filename()),
        current_translation_units,
        ]:
      for translation_unit in get_translation_units():
        try:
          return definition_of_current_cusor_in(translation_unit)
        except NoDefinitionFound:
          pass
    return None

class DefinitionFileFinder(object):
  """
  Given the name of a file (e.g. foo.h), finds similarly named files (e.g. foo.cpp,
  fooI.cpp) somewhere nearby in the file system.
  """
  def __init__(self, editor, target_file_name):
    self.editor = editor
    self.target_file_name = target_file_name
    self.split_target = os.path.splitext(os.path.basename(self.target_file_name))
    self.visited_directories = set()
    self.search_limit = 50
    self.num_directories_searched = 0

  def definition_files(self):
    directory_name = os.path.dirname(self.target_file_name)
    for result in self._search_directory_and_parent_directories(directory_name):
      yield result

  def _search_directory_and_parent_directories(self, directory_name):
    for result in self._search_directory_and_subdirectories(directory_name):
      yield result
    parent_directory_name = os.path.abspath(os.path.join(directory_name, '..'))
    if parent_directory_name != directory_name:
      for result in self._search_directory_and_parent_directories(parent_directory_name):
        yield result

  def _search_directory_and_subdirectories(self, directory_name):
    self.num_directories_searched = 1 + self.num_directories_searched
    if self.num_directories_searched > self.search_limit:
      return
    self.visited_directories.add(os.path.abspath(directory_name))
    try:
      for file_name in os.listdir(directory_name):
        absolute_name = os.path.abspath(os.path.join(directory_name, file_name))
        if os.path.isdir(absolute_name) and file_name not in self.editor.excluded_directories():
          if absolute_name not in self.visited_directories:
            for result in self._search_directory_and_subdirectories(absolute_name):
              yield result
        else:
          if self._is_definition_file_name(file_name):
            yield absolute_name
    except OSError:
      pass

  def _distance(self, a, b):
    return Levenshtein.distance(a, b)

  def _is_definition_file_name(self, file_name):
    split_file_name = os.path.splitext(file_name)
    return (self._distance(split_file_name[0], self.split_target[0]) < 3 and
        split_file_name[1] in ('.cpp', 'c'))


kinds = dict({                                                                 \
# Declarations                                                                 \
 1 : 't',  # CXCursor_UnexposedDecl (A declaration whose specific kind is not  \
           # exposed via this interface)                                       \
 2 : 't',  # CXCursor_StructDecl (A C or C++ struct)                           \
 3 : 't',  # CXCursor_UnionDecl (A C or C++ union)                             \
 4 : 't',  # CXCursor_ClassDecl (A C++ class)                                  \
 5 : 't',  # CXCursor_EnumDecl (An enumeration)                                \
 6 : 'm',  # CXCursor_FieldDecl (A field (in C) or non-static data member      \
           # (in C++) in a struct, union, or C++ class)                        \
 7 : 'e',  # CXCursor_EnumConstantDecl (An enumerator constant)                \
 8 : 'f',  # CXCursor_FunctionDecl (A function)                                \
 9 : 'v',  # CXCursor_VarDecl (A variable)                                     \
10 : 'a',  # CXCursor_ParmDecl (A function or method parameter)                \
11 : '11', # CXCursor_ObjCInterfaceDecl (An Objective-C @interface)            \
12 : '12', # CXCursor_ObjCCategoryDecl (An Objective-C @interface for a        \
           # category)                                                         \
13 : '13', # CXCursor_ObjCProtocolDecl (An Objective-C @protocol declaration)  \
14 : '14', # CXCursor_ObjCPropertyDecl (An Objective-C @property declaration)  \
15 : '15', # CXCursor_ObjCIvarDecl (An Objective-C instance variable)          \
16 : '16', # CXCursor_ObjCInstanceMethodDecl (An Objective-C instance method)  \
17 : '17', # CXCursor_ObjCClassMethodDecl (An Objective-C class method)        \
18 : '18', # CXCursor_ObjCImplementationDec (An Objective-C @implementation)   \
19 : '19', # CXCursor_ObjCCategoryImplDecll (An Objective-C @implementation    \
           # for a category)                                                   \
20 : 't',  # CXCursor_TypedefDecl (A typedef)                                  \
21 : 'f',  # CXCursor_CXXMethod (A C++ class method)                           \
22 : 'n',  # CXCursor_Namespace (A C++ namespace)                              \
23 : '23', # CXCursor_LinkageSpec (A linkage specification, e.g. 'extern "C"') \
24 : '+',  # CXCursor_Constructor (A C++ constructor)                          \
25 : '~',  # CXCursor_Destructor (A C++ destructor)                            \
26 : '26', # CXCursor_ConversionFunction (A C++ conversion function)           \
27 : 'a',  # CXCursor_TemplateTypeParameter (A C++ template type parameter)    \
28 : 'a',  # CXCursor_NonTypeTemplateParameter (A C++ non-type template        \
           # parameter)                                                        \
29 : 'a',  # CXCursor_TemplateTemplateParameter (A C++ template template       \
           # parameter)                                                        \
30 : 'f',  # CXCursor_FunctionTemplate (A C++ function template)               \
31 : 'p',  # CXCursor_ClassTemplate (A C++ class template)                     \
32 : '32', # CXCursor_ClassTemplatePartialSpecialization (A C++ class template \
           # partial specialization)                                           \
33 : 'n',  # CXCursor_NamespaceAlias (A C++ namespace alias declaration)       \
34 : '34', # CXCursor_UsingDirective (A C++ using directive)                   \
35 : '35', # CXCursor_UsingDeclaration (A using declaration)                   \
                                                                               \
# References                                                                   \
40 : '40', # CXCursor_ObjCSuperClassRef                                        \
41 : '41', # CXCursor_ObjCProtocolRef                                          \
42 : '42', # CXCursor_ObjCClassRef                                             \
43 : '43', # CXCursor_TypeRef                                                  \
44 : '44', # CXCursor_CXXBaseSpecifier                                         \
45 : '45', # CXCursor_TemplateRef (A reference to a class template, function   \
           # template, template template parameter, or class template partial  \
           # specialization)                                                   \
46 : '46', # CXCursor_NamespaceRef (A reference to a namespace or namespace    \
           # alias)                                                            \
47 : '47', # CXCursor_MemberRef (A reference to a member of a struct, union,   \
           # or class that occurs in some non-expression context, e.g., a      \
           # designated initializer)                                           \
48 : '48', # CXCursor_LabelRef (A reference to a labeled statement)            \
49 : '49', # CXCursor_OverloadedDeclRef (A reference to a set of overloaded    \
           # functions or function templates that has not yet been resolved to \
           # a specific function or function template)                         \
                                                                               \
# Error conditions                                                             \
#70 : '70', # CXCursor_FirstInvalid                                            \
70 : '70',  # CXCursor_InvalidFile                                             \
71 : '71',  # CXCursor_NoDeclFound                                             \
72 : 'u',   # CXCursor_NotImplemented                                          \
73 : '73',  # CXCursor_InvalidCode                                             \
                                                                               \
# Expressions                                                                  \
100 : '100',  # CXCursor_UnexposedExpr (An expression whose specific kind is   \
              # not exposed via this interface)                                \
101 : '101',  # CXCursor_DeclRefExpr (An expression that refers to some value  \
              # declaration, such as a function, varible, or enumerator)       \
102 : '102',  # CXCursor_MemberRefExpr (An expression that refers to a member  \
              # of a struct, union, class, Objective-C class, etc)             \
103 : '103',  # CXCursor_CallExpr (An expression that calls a function)        \
104 : '104',  # CXCursor_ObjCMessageExpr (An expression that sends a message   \
              # to an Objective-C object or class)                             \
105 : '105',  # CXCursor_BlockExpr (An expression that represents a block      \
              # literal)                                                       \
                                                                               \
# Statements                                                                   \
200 : '200',  # CXCursor_UnexposedStmt (A statement whose specific kind is not \
              # exposed via this interface)                                    \
201 : '201',  # CXCursor_LabelStmt (A labelled statement in a function)        \
                                                                               \
# Translation unit                                                             \
300 : '300',  # CXCursor_TranslationUnit (Cursor that represents the           \
              # translation unit itself)                                       \
                                                                               \
# Attributes                                                                   \
400 : '400',  # CXCursor_UnexposedAttr (An attribute whose specific kind is    \
              # not exposed via this interface)                                \
401 : '401',  # CXCursor_IBActionAttr                                          \
402 : '402',  # CXCursor_IBOutletAttr                                          \
403 : '403',  # CXCursor_IBOutletCollectionAttr                                \
                                                                               \
# Preprocessing                                                                \
500 : '500', # CXCursor_PreprocessingDirective                                 \
501 : 'd',   # CXCursor_MacroDefinition                                        \
502 : '502', # CXCursor_MacroInstantiation                                     \
503 : '503'  # CXCursor_InclusionDirective                                     \
})
# vim: set ts=2 sts=2 sw=2 expandtab :




