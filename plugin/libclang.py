from clang.cindex import *
import vim
import time
import re
import threading

class VimInterface(object):
# Get a tuple (filename, filecontent) for the file opened in the current
# vim buffer. The filecontent contains the unsafed buffer content.
  def current_file(self):
    file = "\n".join(vim.eval("getline(1, '$')"))
    return (self.filename, file)

  def user_options(self):
    user_options_global = vim.eval("g:clang_user_options").split(" ")
    user_options_local = vim.eval("b:clang_user_options").split(" ")
    return user_options_global + user_options_local

  @property
  def filename(self):
    return vim.current.buffer.name

  def open_file(self, filename, line, column):
    vim.command("e +" + str(line) + " " + filename)

  def debug_enabled(self):
    return int(vim.eval("g:clang_debug")) == 1

class EmacsInterface(object):

  def __init__(self):
    import pymacs.lisp as emacs

  def current_file(self):
    return (self.filename, "")

  @property
  def filename(self):
    return emacs.buffer_file_name()

  def user_options():
    return ""

  def open_file(self, filename, line, column):
    emacs.find_file(filename)

  def debug_enabled(self):
    return False

def init_clang_complete(clang_complete_flags):
  global index
  index = Index.create()
  global translation_units
  translation_units = dict()
  global complete_flags
  complete_flags = int(clang_complete_flags)
  global definition_finder
  definition_finder = DefinitionFinder()
  global editor
  editor = VimInterface()

def get_current_translation_unit(update = False):
  args = editor.user_options()

  current_file = editor.current_file()
  filename = editor.filename

  if filename in translation_units:
    tu = translation_units[filename]
    if update:
      if editor.debug_enabled():
        start = time.time()
      tu.reparse([current_file])
      if editor.debug_enabled():
        elapsed = (time.time() - start)
        print "LibClang - Reparsing: " + str(elapsed)
    return tu

  if editor.debug_enabled():
    start = time.time()
  flags = TranslationUnit.PrecompiledPreamble | TranslationUnit.CXXPrecompiledPreamble # | TranslationUnit.CacheCompletionResults
  tu = index.parse(filename, args, [current_file], flags)
  if editor.debug_enabled():
    elapsed = (time.time() - start)
    print "LibClang - First parse: " + str(elapsed)

  if tu == None:
    print "Cannot parse this source file. The following arguments " \
        + "are used for clang: " + " ".join(args)
    return None

  translation_units[filename] = tu

  # Reparse to initialize the PCH cache even for auto completion
  # This should be done by index.parse(), however it is not.
  # So we need to reparse ourselves.
  if editor.debug_enabled():
    start = time.time()
  tu.reparse([current_file])
  if editor.debug_enabled():
    elapsed = (time.time() - start)
    print "LibClang - First reparse (generate PCH cache): " + str(elapsed)
  return tu

def get_quick_fix(diagnostic):
  # Some diagnostics have no file, e.g. "too many errors emitted, stopping now"
  if diagnostic.location.file:
    filename = diagnostic.location.file.name
  else:
    filename = ""

  if diagnostic.severity == diagnostic.Warning:
    type = 'W'
  elif diagnostic.severity == diagnostic.Error:
    type = 'E'
  else:
    return None

  return dict({ 'bufnr' : int(vim.eval("bufnr('" + filename + "', 1)")),
    'lnum' : diagnostic.location.line,
    'col' : diagnostic.location.column,
    'text' : diagnostic.spelling,
    'type' : type})

def get_quick_fix_list(tu):
  return filter (None, map (get_quick_fix, tu.diagnostics))

def highlight_range(range, hg_group):
  pattern = '/\%' + str(range.start.line) + 'l' + '\%' \
      + str(range.start.column) + 'c' + '.*' \
      + '\%' + str(range.end.column) + 'c/'
  command = "exe 'syntax match' . ' " + hg_group + ' ' + pattern + "'"
  vim.command(command)

def highlight_diagnostic(diagnostic):
  if diagnostic.severity == diagnostic.Warning:
    hg_group = 'SpellLocal'
  elif diagnostic.severity == diagnostic.Error:
    hg_group = 'SpellBad'
  else:
    return

  pattern = '/\%' + str(diagnostic.location.line) + 'l\%' \
      + str(diagnostic.location.column) + 'c./'
  command = "exe 'syntax match' . ' " + hg_group + ' ' + pattern + "'"
  vim.command(command)

  # Use this wired kind of iterator as the python clang libraries
        # have a bug in the range iterator that stops us to use:
        #
        # | for range in diagnostic.ranges
        #
  for i in range(len(diagnostic.ranges)):
    highlight_range(diagnostic.ranges[i], hg_group)

def highlight_diagnostics(tu):
  map (highlight_diagnostic, tu.diagnostics)

def highlight_current_diagnostics():
  if editor.filename in translation_units:
    highlight_diagnostics(translation_units[editor.filename])

def get_current_quickfix_list():
  if editor.filename in translation_units:
    return get_quick_fix_list(translation_units[editor.filename])
  return []

def update_current_diagnostics():
  get_current_translation_unit(update = True)

def get_current_completion_results(line, column):
  tu = get_current_translation_unit()
  current_file = editor.current_file()
  if editor.debug_enabled():
    start = time.time()
  cr = tu.codeComplete(editor.filename, line, column, [current_file],
      complete_flags)
  if editor.debug_enabled():
    elapsed = (time.time() - start)
    print "LibClang - Code completion time: " + str(elapsed)
  return cr

def format_results(result):
  completion = dict()

  abbr = get_abbr(result.string)
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


class CompleteThread(threading.Thread):
  lock = threading.Lock()

  def __init__(self, line, column):
    threading.Thread.__init__(self)
    self.line = line
    self.column = column
    self.result = None

  def run(self):
    with CompleteThread.lock:
      try:
        self.result = get_current_completion_results(self.line, self.column)
      except Exception:
        pass

class DefinitionFinder(object):

  def __init__(self):
    self.referencing_translation_units = {}

  class FindDefinitionInTranslationUnit(object):
    def __init__(self, translation_unit, referencing_translation_units):
      self.translation_unit = translation_unit
      self.referencing_translation_units = referencing_translation_units

    def get_current_location(self):
      line = int(vim.eval("line('.')"))
      column = int(vim.eval("col('.')"))
      file = self.translation_unit.getFile(editor.filename)
      if not file:
        return None
      return self.translation_unit.getLocation(file, line, column)

    def get_definition_cursor(self):
      location = self.get_current_location()
      cursor = self.translation_unit.getCursor(location)
      result = cursor.get_definition()
      if result:
        self.store_referencing_translation_unit(result)
      return result

    def store_referencing_translation_unit(self, definition_cursor):
      definition_location = definition_cursor.extent.start
      definition_filename = definition_location.file.name.spelling
      self.referencing_translation_units[definition_filename] = self.translation_unit

  def find_definition_in_translation_unit(self, translation_unit):
    return self.FindDefinitionInTranslationUnit(translation_unit,
        self.referencing_translation_units).get_definition_cursor()

  def jump_to_definition(self):

    definition_cursor = self.find_definition_in_translation_unit(get_current_translation_unit())

    if not definition_cursor:
      try:
        referencing_translation_unit = self.referencing_translation_units[editor.filename]
        definition_cursor = self.find_definition_in_translation_unit(referencing_translation_unit)
      except KeyError:
        print("No definition could be found by parsing this file on its own. We also didn't jump here from another parsed file.")
        pass

    if definition_cursor:
      definition_location = definition_cursor.extent.start
      editor.open_file(definition_location.file.name.spelling,
          definition_location.line, definition_location.column)
    else:
      print("No definition available")

def jump_to_definition():
  return definition_finder.jump_to_definition()

def get_current_completions(base):
  priority = vim.eval("g:clang_sort_algo") == 'priority'
  line = int(vim.eval("line('.')"))
  column = int(vim.eval("b:col"))

  t = CompleteThread(line, column)
  t.start()
  while t.is_alive():
    t.join(0.01)
    cancel = int(vim.eval('complete_check()'))
    if cancel != 0:
      return []
  cr = t.result
  if cr is None:
    return []

  regexp = re.compile("^" + base)
  filtered_result = filter(lambda x: regexp.match(get_abbr(x.string)), cr.results)

  get_priority = lambda x: x.string.priority
  get_abbreviation = lambda x: get_abbr(x.string).lower()
  if priority:
    key = get_priority
  else:
    key = get_abbreviation
  sorted_result = sorted(filtered_result, None, key)
  return map(format_results, sorted_result)

def get_abbr(strings):
  tmplst = filter(lambda x: x.isKindTypedText(), strings)
  if len(tmplst) == 0:
    return ""
  else:
    return tmplst[0].spelling

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
501 : 'm',   # CXCursor_MacroDefinition                                        \
502 : '502', # CXCursor_MacroInstantiation                                     \
503 : '503'  # CXCursor_InclusionDirective                                     \
})

# vim: set ts=2 sts=2 sw=2 expandtab :
