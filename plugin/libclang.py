import clang.cindex
from common import ExportedRange
from finding import DeclarationFinder, DefinitionFinder
from translation_unit_access import TranslationUnitAccessor
import threading
import sys
import actions

"""
Ideas:

    - Highlight methods that don't refer to members (could-be-static)

    - Highlight unused pre-declarations

    - Highlight unused includes (probably not possible)

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
     - There seems to be some confusion between returning NULL or nullCursor
       - get_semantic_parent returns nullCursor
       - get_definition returns NULL
    - Allow jumping through pimpls
    - When opening a new file, right away get possible translation units
     - keep a set of translation unit (name -> translation unit)
      - ensure that accessing this set always uses the most current version of
        the file
     - the current file
     - an alternate file (.h -> .cpp)
     - *not required* referencing translation unit, as we already were there

    - Integrate Jump-To-Definition with tags-based searching
     - Allow finding definitions of commented code
     - Macros
"""


def abort_after_first_call(consumer, producer):
    class ConsumeWasCalled(Exception):
        pass

    def consume_and_abort(x):
        consumer(x)
        raise ConsumeWasCalled
    try:
        producer(consume_and_abort)
    except ConsumeWasCalled:
        pass


def print_cursor_with_children(cursor, n=0):
    sys.stdout.write(n * " ")
    print(str(cursor.kind.name))
    for child in cursor.get_children():
        print_cursor_with_children(child, n + 1)


class EmacsInterface(object):

    def __init__(self):
        from Pymacs import lisp as emacs
        self._emacs = emacs

    def current_file(self):
        return (self.file_name(), self._emacs.buffer_string())

    def file_name(self):
        return self._emacs.buffer_file_name()

    def user_options(self):
        return ""

    def open_file(self, file_name, line, column):
        self._emacs.find_file(file_name)
        self._emacs.goto_line(line)
        self._emacs.move_to_column(column - 1)

    def debug_enabled(self):
        return False

    def current_line(self):
        return self._emacs.line_number_at_pos()

    def current_column(self):
        return 1 + self._emacs.current_column()

    def display_message(self, message):
        self._emacs.minibuffer_message(message)


class ClangPlugin(object):
    def __init__(self, editor, clang_complete_flags, library_path):

        if not clang.cindex.Config.loaded:
            if library_path != "":
                clang.cindex.Config.set_library_path(library_path)

            clang.cindex.Config.set_compatibility_check(False)

        self._editor = editor
        self._translation_unit_accessor = TranslationUnitAccessor(self._editor)
        self._definition_finder = DefinitionFinder(
            self._editor, self._translation_unit_accessor)
        self._declaration_finder = DeclarationFinder(
            self._editor, self._translation_unit_accessor)
        self._completer = Completer(self._editor, self._translation_unit_accessor, int(clang_complete_flags))
        self._quick_fix_list_generator = QuickFixListGenerator(self._editor)
        self._diagnostics_highlighter = DiagnosticsHighlighter(self._editor)
        self._file_has_changed = True
        self._file_at_last_change = None

    def terminate(self):
        self._translation_unit_accessor.terminate()

    def _start_rescan(self):
        self._translation_unit_accessor.clear_caches()
        self._load_files_in_background()

    def file_changed(self):
        self._editor.display_message(
            "File change was notified, clearing all caches.")
        self._start_rescan()
        self._file_has_changed = True
        self._file_at_last_change = self._editor.current_file()

    def _highlight_interesting_ranges(self, translation_unit):

        class MemoizedTranslationUnit(object):
            def __init__(self, translation_unit):
                self.cursor = translation_unit.cursor
                self.spelling = translation_unit.spelling
        memoized_translation_unit = MemoizedTranslationUnit(translation_unit)

        styles_and_actions = [
            ("Non-const reference", actions.FindParametersPassedByNonConstReferenceAction(self._editor)),
            ("Virtual method declaration",
                actions.FindVirtualMethodDeclarationsAction()),
            ("Static method declaration",
                actions.FindStaticMethodDeclarationsAction()),
            ("Member reference", actions.FindMemberReferencesAction())]
                #("Virtual method call", actions.FindVirtualMethodCallsAction()),
                #("Omitted default argument", actions.FindOmittedDefaultArgumentsAction())]

        for highlight_style, action in styles_and_actions:
            self._editor.clear_highlights(highlight_style)
            ranges = action.find_ranges(memoized_translation_unit)
            for range in ranges:
                self._highlight_range_if_in_current_file(
                    range, highlight_style)

    def tick(self):
        if self._file_has_changed:

            def do_it(translation_unit):
                self._editor.display_diagnostics(self._quick_fix_list_generator.get_quick_fix_list(translation_unit))
                self._diagnostics_highlighter.highlight_in_translation_unit(
                    translation_unit)
                #self._highlight_interesting_ranges(translation_unit)

                self._file_has_changed = self._editor.current_file() != self._file_at_last_change

            self._translation_unit_accessor.current_translation_unit_if_parsed_do(do_it)

    def file_opened(self):
        self._editor.display_message("Noticed opening of new file")
        # Why clear on opening, closing is enough.
        self._load_files_in_background()

    def _load_files_in_background(self):
        self._translation_unit_accessor.enqueue_translation_unit_creation(
            self._editor.current_file())

    def jump_to_definition(self):
        #self._editor.user_abortable_perform(
            #functools.partial(abort_after_first_call, self._editor.open_location),
            #self._definition_finder.definition_locations_do)
        abort_after_first_call(self._editor.open_location,
                               self._definition_finder.definition_locations_do)

    def jump_to_declaration(self):
        abort_after_first_call(self._editor.open_location,
                               self._declaration_finder.declaration_locations_do)

    def get_current_completions(self, base):
        "TODO: This must be synchronized as well, but as it runs in a separate thread it gets a bit more complete"
        return self._completer.get_current_completions(base)

    def find_references_to_outside_of_selection(self):
        def do_it(translation_unit):
            return actions.FindReferencesToOutsideOfSelectionAction().find_references_to_outside_of_selection(
                translation_unit,
                self._editor.selection())
        return self._translation_unit_accessor.current_translation_unit_do(do_it)

    def _highlight_range_if_in_current_file(self, range, highlight_style):
        if range.start.file_name == self._editor.file_name():
            self._editor.highlight_range(range, highlight_style)

    def highlight_references_to_outside_of_selection(self):
        references = self.find_references_to_outside_of_selection()

        style_referenced_range = "Referenced Range"
        style_referencing_range = "Referencing Range"
        self._editor.clear_highlights(style_referenced_range)
        self._editor.clear_highlights(style_referencing_range)
        for reference in references:
            self._highlight_range_if_in_current_file(reference.referenced_range, style_referenced_range)
            self._highlight_range_if_in_current_file(reference.referencing_range, style_referencing_range)

        qf = [dict({'filename': reference.referenced_range.start.file_name,
                    'lnum': reference.referenced_range.start.line,
                    'col': reference.referenced_range.start.column,
                    'text': 'Reference'}) for reference in references if reference.referenced_range.start.file_name == self._editor.file_name()]

        self._editor.display_diagnostics(qf)


class DiagnosticsHighlighter(object):

    def __init__(self, editor):
        self._editor = editor
        self._highlight_style = "Diagnostic"

    def _highlight_diagnostic(self, diagnostic):

        if diagnostic.severity not in (diagnostic.Warning, diagnostic.Error, diagnostic.Note):
            return

        single_location_range = ExportedRange(
            diagnostic.location, diagnostic.location)
        self._editor.highlight_range(
            single_location_range, self._highlight_style)

        for range in diagnostic.ranges:
            self._editor.highlight_range(range, self._highlight_style)

    def highlight_in_translation_unit(self, translation_unit):
        self._editor.clear_highlights(self._highlight_style)
        map(self._highlight_diagnostic, translation_unit.diagnostics)


class QuickFixListGenerator(object):

    def __init__(self, editor):
        self._editor = editor

    def _get_quick_fix(self, diagnostic):
        # Some diagnostics have no file, e.g. "too many errors emitted, stopping now"
        if diagnostic.location.file:
            file_name = diagnostic.location.file.name
        else:
            "hack: report errors without files. should nevertheless be in quick_fix list"
            self._editor.display_message(diagnostic.spelling)
            file_name = ""

        if diagnostic.severity == diagnostic.Ignored:
            type = 'I'
        elif diagnostic.severity == diagnostic.Note:
            type = 'I'
        elif diagnostic.severity == diagnostic.Warning:
            if "argument unused during compilation" in diagnostic.spelling:
                return None
            type = 'W'
        elif diagnostic.severity == diagnostic.Error:
            type = 'E'
        elif diagnostic.severity == diagnostic.Fatal:
            type = 'E'
        else:
            type = 'O'

        return dict({'filename': file_name,
                     'lnum': diagnostic.location.line,
                     'col': diagnostic.location.column,
                     'text': diagnostic.spelling,
                     'type': type})

    def get_quick_fix_list(self, tu):
        return filter(None, map(self._get_quick_fix, tu.diagnostics))


class Completer(object):

    def __init__(self, editor, translation_unit_accessor, complete_flags):
        self._editor = editor
        self._translation_unit_accessor = translation_unit_accessor
        self._complete_flags = complete_flags

    def format_results(self, result):
        completion = dict()
        return_value = None
        abbr = ""
        args_pos = []
        cur_pos = 0
        word = ""

        for chunk in result.string:

            if chunk.isKindInformative():
                continue

            if chunk.isKindResultType():
                return_value = chunk
                continue

            chunk_spelling = chunk.spelling

            if chunk.isKindTypedText():
                abbr = chunk_spelling

            chunk_len = len(chunk_spelling)
            if chunk.isKindPlaceHolder():
                args_pos += [[cur_pos, cur_pos + chunk_len]]
            cur_pos += chunk_len
            word += chunk_spelling

        menu = word

        if return_value:
            menu = return_value.spelling + " " + menu

        completion['word'] = word
        completion['abbr'] = abbr
        completion['menu'] = menu
        completion['info'] = word
        completion['args_pos'] = args_pos
        completion['dup'] = 1

        # Replace the number that represents a specific kind with a better
        # textual representation.
        completion['kind'] = kinds[result.cursorKind]

        return completion

    def get_current_completions(self, base):

        sorting = self._editor.sort_algorithm()

        thread = CompleteThread(self._editor,
                                self._translation_unit_accessor,
                                self._complete_flags,
                                self._editor.current_line(),
                                self._editor.current_column())

        thread.start()
        while thread.is_alive():
            thread.join(0.01)
            if self._editor.abort_requested():
                return []
        completionResult = thread.result
        if completionResult is None:
            return []

        results = completionResult.results

        if base != "":
            results = filter(lambda x: self.get_abbr(x.string).startswith(base), results)

        if sorting == 'priority':
            get_priority = lambda x: x.string.priority
            key = get_priority
            results = sorted(results, None, key)
        if sorting == 'alpha':
            get_abbreviation = lambda x: self.get_abbr(x.string).lower()
            key = get_abbreviation
            results = sorted(results, None, key)
        return map(self.format_results, results)

    def get_abbr(self, strings):
        for chunks in strings:
            if chunks.isKindTypedText():
                return chunks.spelling
            return ""


class CompleteThread(threading.Thread):
    lock = threading.Lock()

    def __init__(self, editor, translation_unit_accessor, complete_flags, line, column):
        threading.Thread.__init__(self)
        self._editor = editor
        self._complete_flags = complete_flags
        self._line = line
        self._column = column
        self._translation_unit_accessor = translation_unit_accessor
        self._current_file = editor.current_file()
        self._file_name = editor.file_name()

        self.result = None

    def run(self):
        try:
            CompleteThread.lock.acquire()
            self.result = self.get_current_completion_results(
                self._line, self._column)
        except Exception, e:
            self._editor.display_message("Exception thrown in completion thread: " + str(e))
        finally:
            CompleteThread.lock.release()

    def get_current_completion_results(self, line, column):
        def _do_it(translation_unit):
            return translation_unit.codeComplete(
                self._file_name, line, column, [self._current_file], self._complete_flags)

        return self._translation_unit_accessor.translation_unit_do(self._current_file, _do_it)


kinds = dict({
             # Declarations
             1: 't',  # CXCursor_UnexposedDecl (A declaration whose specific kind is not
             # exposed via this interface)
             2: 't',  # CXCursor_StructDecl (A C or C++ struct)
             3: 't',  # CXCursor_UnionDecl (A C or C++ union)
             4: 't',  # CXCursor_ClassDecl (A C++ class)
             5: 't',  # CXCursor_EnumDecl (An enumeration)
             6: 'm',  # CXCursor_FieldDecl (A field (in C) or non-static data member
             # (in C++) in a struct, union, or C++ class)
             7: 'e',  # CXCursor_EnumConstantDecl (An enumerator constant)
             8: 'f',  # CXCursor_FunctionDecl (A function)
             9: 'v',  # CXCursor_VarDecl (A variable)
             10: 'a',  # CXCursor_ParmDecl (A function or method parameter)
             11: '11',  # CXCursor_ObjCInterfaceDecl (An Objective-C @interface)
             12: '12',  # CXCursor_ObjCCategoryDecl (An Objective-C @interface for a
             # category)
             13: '13',  # CXCursor_ObjCProtocolDecl (An Objective-C @protocol declaration)
             14: '14',  # CXCursor_ObjCPropertyDecl (An Objective-C @property declaration)
             15: '15',  # CXCursor_ObjCIvarDecl (An Objective-C instance variable)
             16: '16',  # CXCursor_ObjCInstanceMethodDecl (An Objective-C instance method)
             17: '17',  # CXCursor_ObjCClassMethodDecl (An Objective-C class method)
             18: '18',  # CXCursor_ObjCImplementationDec (An Objective-C @implementation)
             19: '19',  # CXCursor_ObjCCategoryImplDecll (An Objective-C @implementation
             # for a category)
             20: 't',  # CXCursor_TypedefDecl (A typedef)
             21: 'f',  # CXCursor_CXXMethod (A C++ class method)
             22: 'n',  # CXCursor_Namespace (A C++ namespace)
             23: '23',  # CXCursor_LinkageSpec (A linkage specification, e.g. 'extern "C"')
             24: '+',  # CXCursor_Constructor (A C++ constructor)
             25: '~',  # CXCursor_Destructor (A C++ destructor)
             26: '26',  # CXCursor_ConversionFunction (A C++ conversion function)
             27: 'a',  # CXCursor_TemplateTypeParameter (A C++ template type parameter)
             28: 'a',  # CXCursor_NonTypeTemplateParameter (A C++ non-type template
             # parameter)
             29: 'a',  # CXCursor_TemplateTemplateParameter (A C++ template template
             # parameter)
             30: 'f',  # CXCursor_FunctionTemplate (A C++ function template)
             31: 'p',  # CXCursor_ClassTemplate (A C++ class template)
             32: '32',  # CXCursor_ClassTemplatePartialSpecialization (A C++ class template
             # partial specialization)
             33: 'n',  # CXCursor_NamespaceAlias (A C++ namespace alias declaration)
             34: '34',  # CXCursor_UsingDirective (A C++ using directive)
             35: '35',  # CXCursor_UsingDeclaration (A using declaration)
                                                                               \
             # References
             40: '40',  # CXCursor_ObjCSuperClassRef
             41: '41',  # CXCursor_ObjCProtocolRef
             42: '42',  # CXCursor_ObjCClassRef
             43: '43',  # CXCursor_TypeRef
             44: '44',  # CXCursor_CXXBaseSpecifier
             45: '45',  # CXCursor_TemplateRef (A reference to a class template, function
             # template, template template parameter, or class template partial
             # specialization)
             46: '46',  # CXCursor_NamespaceRef (A reference to a namespace or namespace
             # alias)
             47: '47',  # CXCursor_MemberRef (A reference to a member of a struct, union,
             # or class that occurs in some non-expression context, e.g., a
             # designated initializer)
             48: '48',  # CXCursor_LabelRef (A reference to a labeled statement)
             49: '49',  # CXCursor_OverloadedDeclRef (A reference to a set of overloaded
             # functions or function templates that has not yet been resolved to
             # a specific function or function template)
                                                                               \
             # Error conditions
             #70 : '70', # CXCursor_FirstInvalid
             70: '70',  # CXCursor_InvalidFile
             71: '71',  # CXCursor_NoDeclFound
             72: 'u',   # CXCursor_NotImplemented
             73: '73',  # CXCursor_InvalidCode
                                                                               \
             # Expressions
             100: '100',  # CXCursor_UnexposedExpr (An expression whose specific kind is
             # not exposed via this interface)
             101: '101',  # CXCursor_DeclRefExpr (An expression that refers to some value
             # declaration, such as a function, varible, or enumerator)
             102: '102',  # CXCursor_MemberRefExpr (An expression that refers to a member
             # of a struct, union, class, Objective-C class, etc)
             103: '103',  # CXCursor_CallExpr (An expression that calls a function)
             104: '104',  # CXCursor_ObjCMessageExpr (An expression that sends a message
             # to an Objective-C object or class)
             105: '105',  # CXCursor_BlockExpr (An expression that represents a block
             # literal)
                                                                               \
             # Statements
             200: '200',  # CXCursor_UnexposedStmt (A statement whose specific kind is not
             # exposed via this interface)
             201: '201',  # CXCursor_LabelStmt (A labelled statement in a function)
                                                                               \
             # Translation unit
             300: '300',  # CXCursor_TranslationUnit (Cursor that represents the
             # translation unit itself)
                                                                               \
             # Attributes
             400: '400',  # CXCursor_UnexposedAttr (An attribute whose specific kind is
             # not exposed via this interface)
             401: '401',  # CXCursor_IBActionAttr
             402: '402',  # CXCursor_IBOutletAttr
             403: '403',  # CXCursor_IBOutletCollectionAttr
                                                                               \
             # Preprocessing
             500: '500',  # CXCursor_PreprocessingDirective
             501: 'd',   # CXCursor_MacroDefinition
             502: '502',  # CXCursor_MacroInstantiation
             503: '503'  # CXCursor_InclusionDirective
             })
