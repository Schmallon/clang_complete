import clang.cindex
import re
import threading
import os
import sys
import Levenshtein
import Queue
import traceback
import time
import functools

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
    - ensure that accessing this set always uses the most current version of the file
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


def get_file_for_file_name(file_name):
    return (file_name, open(file_name, 'r').read())


def print_cursor_with_children(cursor, n=0):
    sys.stdout.write(n * " ")
    print(str(cursor.kind.name))
    for child in cursor.get_children():
        print_cursor_with_children(child, n + 1)


class VimInterface(object):

    """Abortable perform doesn't yet work. We must stay within one OS-thread. TODO:
      Find some green-thread implementation for python"""
    def user_abortable_perform(self, consumer, producer):
        stop_running = False

        def pass_to_consumer_if_not_aborted(result):
            if not stop_running:
                consumer(result)
            stop_running = True

        def do_it():
            producer(pass_to_consumer_if_not_aborted)

        threading.Thread(target=do_it).start()
        while not stop_running:
            try:
                self.eval("getchar(0)")
            except:
                stop_running = True
            time.sleep(1)

    class LoggingVim(object):
        def __init__(self, logger):
            import vim
            self._vim = vim
            self._logger = logger
            self._creator_thread = threading.currentThread()

        def _check_thread(self, command):
            current_thread = threading.currentThread()
            if self._creator_thread != current_thread:
                self._logger.display_message("Warning: Calling vim command %s from different thread: %s" % (command, current_thread.getName()))
                self._logger.print_stack()

        def eval(self, x):
            self._check_thread("eval(%s)" % str(x))
            self._logger.display_message(str(x))
            result = self._vim.eval(x)
            self._logger.display_message("Succeeded")
            return result

        def command(self, x):
            self._check_thread("command(%s)" % str(x))
            self._logger.display_message(str(x))
            result = self._vim.command(x)
            self._logger.display_message("Succeeded")
            return result

        def current(self):
            self._check_thread("current")
            return self._vim.current

    def __init__(self):
        self._vim = self.LoggingVim(self)
        self._id_to_highlight_group = {
            'Diagnostic' : {'group': 'clang_diagnostic', 'default': 'gui=undercurl guisp=Red'},
            "Non-const reference" : {'group': 'clang_non_const_reference', 'default': 'ctermbg=6 guibg=Yellow'},
            "Virtual method call" : {'group': 'clang_virtual_method_call', 'default' : 'guibg=LightRed'},
            "Virtual method declaration" : {'group': 'clang_virtual_method_declaration', 'default' : 'guibg=LightRed'},
            "Static method declaration" : {'group':  'clang_static_method_declaration', 'default' : 'gui=underline'},
            "Member reference" : {'group': 'clang_member_reference', 'default' : 'gui=bold guifg=#005079 guibg=#DBF2FF'},
            "Referenced Range" : {'group': 'clang_referenced_range', 'default' : 'gui=bold guifg=#FFFF00 guibg=#0000FF', 'priority' : '-10'},
            "Referencing Range" : {'group': 'clang_referencing_range', 'default' : 'gui=bold guifg=#00FFFF guibg=#FF0000', 'priority' : '-5'},
            "Omitted default argument" : {'group': 'clang_omitted_default_argument', 'default': 'ctermbg=6 gui=undercurl guisp=DarkCyan'}}

        self._cached_variable_names = ["g:clang_user_options", "b:clang_user_options", "g:clang_excluded_directories"]
        self._cached_variables = {}
        self.refresh_variables()
        self.init_highlight_groups()

    def init_highlight_groups(self):
        for group in self._id_to_highlight_group.values():
            if self._vim.eval("hlexists('%s')" % group['group']):
                self._vim.command("highlight %(group)s %(default)s" % group)

    def refresh_variables(self):
        for variable_name in self._cached_variable_names:
            self._cached_variables[variable_name] = self._get_uncached_variable(variable_name)

    # Get a tuple (file_name, filecontent) for the file opened in the current
    # vim buffer. The filecontent contains the unsafed buffer content.
    def current_file(self):
        file = "\n".join(self._vim.eval("getline(1, '$')"))
        return (self.file_name(), file)

    def _get_uncached_variable(self, variable_name, default_value=""):
        try:
            if int(self._vim.eval("exists('" + variable_name + "')")):
                return self._vim.eval(variable_name)
            else:
                return default_value
        except:
            return default_value

    def _get_variable(self, variable_name):
        return self._cached_variables[variable_name]

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

    def file_name(self):
        return self._vim.current().buffer.name

    def open_location(self, location):
        self.open_file(location.file.name, location.line, location.column)

    def open_file(self, file_name, line, column):
        if self.file_name() == file_name:
            self._vim.command("normal " + str(line) + "G")
        else:
            self._vim.command("e +" + str(line) + " " + file_name)
        self._vim.command("normal 0")
        if column > 1:
            self._vim.command("normal " + str(column - 1) + "l")

    def debug_enabled(self):
        return int(self._vim.eval("g:clang_debug")) == 1

    def current_location(self):
        return ExportedLocation(self.file_name(), self.current_line(), self.current_column())

    def current_line(self):
        return int(self._vim.eval("line('.')"))

    def current_column(self):
        return int(self._vim.eval("col('.')"))

    def selection(self):
        selection_start = ExportedLocation(
            self.file_name(),
            int(self._vim.eval('line("\'<")')),
            int(self._vim.eval('col("\'<")')))
        selection_end = ExportedLocation(
            self.file_name(),
            int(self._vim.eval('line("\'>")')),
              int(self._vim.eval('col("\'>")')))
        result = ExportedRange(selection_start, selection_end)
        return result

    def sort_algorithm(self):
        return self._vim.eval("g:clang_sort_algo")

    def abort_requested(self):
        return 0 != int(self._vim.eval('complete_check()'))

    def display_message(self, message):
        self._print_to_file(message)

    def _log_file(self):
        return open("clang_log.txt", "a")

    def _print_to_file(self, message):
        with self._log_file() as f:
            f.write(str(time.time()) + " - ")
            f.write(message + "\n")

    def print_stack(self):
        with self._log_file() as f:
            traceback.print_stack(file=f)

    def _display_in_editor(self, message):
        print(message)

    def _go_to(self, line, column):
        self._vim.command("normal " + str(line) + "G")
        self._vim.command("normal " + "0")
        if column > 1:
            self._vim.command("normal " + (column - 1) * "l")

    def select(self, start_line, start_column, end_line, end_column):
        self._go_to(start_line, start_column)
        self._vim.command("normal " + "v")
        self._go_to(end_line, end_column)

    def clear_highlights(self, highlight_style):
        "Assumes that (group -> highlight_style) is injective"
        self._vim.command("syntax clear %s" % self._highlight_group_for_id(highlight_style))

    def highlight_range(self, range, highlight_style):
        self.highlight(range.start.line, range.start.column, range.end.line, range.end.column, highlight_style)

    def highlight(self, start_line, start_column, end_line, end_column, highlight_style):
        pattern = '\%' + str(start_line) + 'l' + '\%' \
            + str(start_column) + 'c' + '.*' \
            + '\%' + str(end_column) + 'c'
        group = self._highlight_group_for_id(highlight_style)
        self._vim.command("syntax match %s /%s/" % (group, pattern))

    def _python_dict_to_vim_dict(self, dictionary):
        def escape(entry):
            return str(entry).replace("\\", "\\\\").replace('"', '\\"')

        def translate_entry(entry):
            return '"' + escape(entry) + '" : "' + escape(dictionary[entry]) + '"'
        return '{' + ','.join(map(translate_entry, dictionary)) + '}'

    def _quick_fix_list_to_str(self, quick_fix_list):
        return '[' + ','.join(map(self._python_dict_to_vim_dict, quick_fix_list)) + ']'

    def display_diagnostics(self, quick_fix_list):
        self._vim.command("call g:CalledFromPythonClangDisplayQuickFix(" + self._quick_fix_list_to_str(quick_fix_list) + ")")

    def _highlight_group_for_id(self, id):
        return self._id_to_highlight_group[id]["group"]

    def _priority_for_id(self, id):
        try:
            return self._id_to_highlight_group[id]["priority"]
        except KeyError:
            return str(-100)


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
    def __init__(self, editor, clang_complete_flags):
        self._editor = editor
        self._translation_unit_accessor = TranslationUnitAccessor(self._editor)
        self._definition_finder = DefinitionFinder(self._editor, self._translation_unit_accessor)
        self._declaration_finder = DeclarationFinder(self._editor, self._translation_unit_accessor)
        self._completer = Completer(self._editor, self._translation_unit_accessor, int(clang_complete_flags))
        self._quick_fix_list_generator = QuickFixListGenerator(self._editor)
        self._diagnostics_highlighter = DiagnosticsHighlighter(self._editor)

    def terminate(self):
        self._translation_unit_accessor.terminate()

    def file_changed(self):
        self._editor.display_message("File change was notified, clearing all caches.")
        self._translation_unit_accessor.clear_caches()
        self._load_files_in_background()

    def _highlight_interesting_ranges(self, translation_unit):

        class MemoizedTranslationUnit(object):
            def __init__(self, translation_unit):
                self.cursor = translation_unit.cursor
                self.spelling = translation_unit.spelling
        memoized_translation_unit = MemoizedTranslationUnit(translation_unit)

        styles_and_actions = [
            ("Non-const reference", FindParametersPassedByNonConstReferenceAction(self._editor)),
            ("Virtual method declaration", FindVirtualMethodDeclarationsAction()),
            ("Static method declaration", FindStaticMethodDeclarationsAction()),
            ("Member reference", FindMemberReferencesAction())]
                #("Virtual method call", FindVirtualMethodCallsAction()),
                #("Omitted default argument", FindOmittedDefaultArgumentsAction())]

        for highlight_style, action in styles_and_actions:
            self._editor.clear_highlights(highlight_style)
            ranges = action.find_ranges(memoized_translation_unit)
            for range in ranges:
                self._highlight_range_if_in_current_file(range, highlight_style)

    def try_update_diagnostics(self):
        self._editor.display_message("Trying to update diagnostics")

        class Success(Exception):
            pass

        def do_it(translation_unit):
            self._editor.display_diagnostics(self._quick_fix_list_generator.get_quick_fix_list(translation_unit))
            self._diagnostics_highlighter.highlight_in_translation_unit(translation_unit)
            #self._highlight_interesting_ranges(translation_unit)
            raise Success()

        try:
            self._translation_unit_accessor.current_translation_unit_if_parsed_do(do_it)
        except Success:
            return 1
        return 0

    def file_opened(self):
        self._editor.display_message("Noticed opening of new file")
        # Why clear on opening, closing is enough.
        self._load_files_in_background()

    def _load_files_in_background(self):
        self._translation_unit_accessor.enqueue_translation_unit_creation(self._editor.current_file())

    def jump_to_definition(self):
        #self._editor.user_abortable_perform(
            #functools.partial(abort_after_first_call, self._editor.open_location),
            #self._definition_finder.definition_locations_do)
        abort_after_first_call(self._editor.open_location, self._definition_finder.definition_locations_do)

    def jump_to_declaration(self):
        abort_after_first_call(self._editor.open_location, self._declaration_finder.declaration_locations_do)

    def get_current_completions(self, base):
        "TODO: This must be synchronized as well, but as it runs in a separate thread it gets a bit more complete"
        return self._completer.get_current_completions(base)

    def find_references_to_outside_of_selection(self):
        def do_it(translation_unit):
            return FindReferencesToOutsideOfSelectionAction().find_references_to_outside_of_selection(
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

        qf = [dict({ 'filename' : reference.referenced_range.start.file_name,
          'lnum' : reference.referenced_range.start.line,
          'col' : reference.referenced_range.start.column,
          'text' : 'Reference'}) for reference in references if reference.referenced_range.start.file_name == self._editor.file_name()]

        self._editor.display_diagnostics(qf)


class ExportedLocation(object):
    def __init__(self, file_name, line, column):
        self.file_name = file_name
        self.line = line
        self.column = column

    def __eq__(self, other):
        return (self.file_name == other.file_name and self.line == other.line and self.column == other.column)

    def __repr__(self):
        return "(%r, %r, %r)" % (self.file_name, self.line, self.column)

    def __hash__(self):
        return self.line * 80 + self.column

    def clang_location(self, translation_unit):
        file = translation_unit.getFile(self.file_name)
        if not file:
            return None
        return translation_unit.getLocation(file, self.line, self.column)

    @classmethod
    def from_clang_location(cls, clang_location):
        return cls(clang_location.file.name if clang_location.file else None, clang_location.line, clang_location.column)


class ExportedRange(object):
    def __init__(self, start, end):
        self.start = start
        self.end = end

    def __eq__(self, other):
        return (self.start == other.start and self.end == other.end)

    def __repr__(self):
        return "(%r, %r)" % (self.start, self.end)

    def __hash__(self):
        return self.start.__hash__() + self.end.__hash__()

    @classmethod
    def from_clang_range(cls, clang_range):
        return cls(ExportedLocation.from_clang_location(clang_range.start), ExportedLocation.from_clang_location(clang_range.end))


class FindReferencesToOutsideOfSelectionAction(object):

    def find_references_to_outside_of_selection(self, translation_unit, selection_range):

        def location_lt(location1, location2):
            return location1.line < location2.line or (
                location1.line == location2.line and location1.column < location2.column)

        def disjoint_with_selection(cursor):
            return (location_lt(cursor.extent.end, selection_range.start)
                or location_lt(selection_range.end, cursor.extent.start))

        def intersects_with_selection(cursor):
            return not disjoint_with_selection(cursor)

        def do_it(cursor, result):

            class Reference(object):
                def __init__(self, referenced_range, referencing_range):
                    self.referenced_range = referenced_range
                    self.referencing_range = referencing_range

            referenced_cursor = get_definition_or_reference(cursor)
            if referenced_cursor:
                if not intersects_with_selection(referenced_cursor):
                    # Limit the extent to start at the name
                    constrained_extent = ExportedRange(
                        ExportedLocation.from_clang_location(referenced_cursor.location),
                        ExportedLocation.from_clang_location(referenced_cursor.extent.end))
                    result.add(Reference(
                      constrained_extent,
                      ExportedRange.from_clang_range(cursor.extent)))

            for child in cursor.get_children():
                if intersects_with_selection(child):
                    do_it(child, result)

        result = set()
        do_it(translation_unit.cursor, result)
        return result


class FindVirtualMethodCallsAction(object):
    def find_ranges(self, translation_unit):
        def do_it(call_expr):
            cursor_referenced = call_expr.get_cursor_referenced()
            if cursor_referenced and cursor_referenced.is_virtual():
                result.add(ExportedRange.from_clang_range(call_expr.extent))

        result = set()
        call_expressions_in_file_of_translation_unit_do(do_it, translation_unit)
        return result


class FindVirtualMethodDeclarationsAction(object):
    def find_ranges(self, translation_unit):
        def do_it(cursor):
            if cursor.is_virtual():
                result.add(ExportedRange.from_clang_range(cursor.identifier_range))

        result = set()
        cursors_of_kind_in_file_of_translation_unit_do(do_it, translation_unit, clang.cindex.CursorKind.CXX_METHOD)
        return result


class FindPrivateMethodDeclarationsAction(object):
    def find_ranges(self, translation_unit):
        def do_it(cursor):
            if cursor.is_static():
                result.add(ExportedRange.from_clang_range(cursor.identifier_range))

        result = set()
        cursors_of_kind_in_file_of_translation_unit_do(do_it, translation_unit, clang.cindex.CursorKind.CXX_METHOD)
        return result


class FindStaticMethodDeclarationsAction(object):
    def find_ranges(self, translation_unit):
        def do_it(cursor):
            if cursor.is_static():
                result.add(ExportedRange.from_clang_range(cursor.identifier_range))

        result = set()
        cursors_of_kind_in_file_of_translation_unit_do(do_it, translation_unit, clang.cindex.CursorKind.CXX_METHOD)
        return result


class FindMemberReferencesAction(object):
    def find_ranges(self, translation_unit):
        class Run(object):
            def __init__(self, translation_unit):
                self.result = set()

            def run(self, cursor, recurse):
                if cursor.kind == clang.cindex.CursorKind.MEMBER_REF_EXPR:
                    if cursor.is_implicit_access():
                        self.result.add(ExportedRange.from_clang_range(cursor.identifier_range))
                recurse()

        run = Run(translation_unit)
        cursors_in_file_of_translation_unit_do(run.run, translation_unit)
        return run.result


class FindOmittedDefaultArgumentsAction(object):

    def _omits_default_argument(self, cursor):
        """
        This implementation relies on default arguments being represented as
        cursors without extent. This is not ideal and is intended to serve only as
        an intermediate solution.
        """
        for argument in cursor.get_args():
            if argument.extent.start.offset == 0 and argument.extent.end.offset == 0:
                return True
        return False

    def find_ranges(self, translation_unit):
        def do_it(call_expr):
            if self._omits_default_argument(call_expr):
                result.add(ExportedRange.from_clang_range(call_expr.extent))

        result = set()
        call_expressions_in_file_of_translation_unit_do(do_it, translation_unit)
        return result


def call_expressions_in_file_of_translation_unit_do(do_it, translation_unit):
    return cursors_of_kind_in_file_of_translation_unit_do(do_it, translation_unit, clang.cindex.CursorKind.CALL_EXPR)


def cursors_of_kind_in_file_of_translation_unit_do(do_it, translation_unit, kind):
    def f(cursor, recurse):
        recurse()
        if cursor.kind == kind:
            do_it(cursor)
    return cursors_in_file_of_translation_unit_do(f, translation_unit)


def cursors_in_file_of_translation_unit_do(do_it, translation_unit):
    def recurse(cursor):
        def recurse_further():
            for child in cursor.get_children():
                recurse(child)
        do_it(cursor, recurse_further)

    for top_level_cursor in translation_unit.cursor.get_children():
        if top_level_cursor.location.file and top_level_cursor.location.file.name == translation_unit.spelling:
            recurse(top_level_cursor)


class FindParametersPassedByNonConstReferenceAction(object):

    def __init__(self, editor):
        self._editor = editor

    def _get_nonconst_reference_param_indexes(self, function_decl_cursor):
        result = []
        param_decls = filter(lambda cursor: cursor.kind == clang.cindex.CursorKind.PARM_DECL, function_decl_cursor.get_children())
        for index, cursor in enumerate(param_decls):
            if cursor.kind == clang.cindex.CursorKind.PARM_DECL:
                if cursor.type.kind in [clang.cindex.TypeKind.LVALUEREFERENCE, clang.cindex.TypeKind.RVALUEREFERENCE]:
                    if not cursor.type.get_pointee().is_const_qualified():
                        result.append(index)
        return result

    def _handle_call_expression(self, result, cursor):
        cursor_referenced = cursor.get_cursor_referenced()
        if cursor_referenced:
            args = list(cursor.get_args())
            for i in self._get_nonconst_reference_param_indexes(cursor_referenced):
                try:
                    result.add(ExportedRange.from_clang_range(args[i].extent))
                except IndexError:
                    self._editor.display_message("Could not find parameter " + str(i) + " in " + str(cursor.extent))

    def find_ranges(self, translation_unit):
        result = set()
        call_expressions_in_file_of_translation_unit_do(
            lambda cursor: self._handle_call_expression(result, cursor),
            translation_unit)
        return result


class NoCurrentTranslationUnit(Exception):
    pass


class TranslationUnitParsingAction(object):
    def __init__(self, editor, index, translation_units, up_to_date, file):
        self._editor = editor
        self._index = index
        self._translation_units = translation_units
        self._up_to_date = up_to_date
        self._file = file

    def parse(self):
        if self._file_name() in self._translation_units:
            result = self._reuse_existing_translation_unit()
        else:
            result = self._read_new_translation_unit()
        self._up_to_date.add(self._file_name())
        return result

    def _file_name(self):
        return self._file[0]

    def _reuse_existing_translation_unit(self):
        tu = self._translation_units[self._file_name()]
        if self._file_name() not in self._up_to_date:
            tu.reparse([self._file])
        return tu

    def _read_new_translation_unit(self):
        flags = clang.cindex.TranslationUnit.PrecompiledPreamble | clang.cindex.TranslationUnit.CXXPrecompiledPreamble | clang.cindex.TranslationUnit.CacheCompletionResults

        args = self._editor.user_options()
        tu = self._index.parse(self._file_name(), args, [self._file], flags)

        if tu == None:
            self._editor.display_message("Cannot parse this source file. The following arguments " \
                + "are used for clang: " + " ".join(args))
            return None

        self._translation_units[self._file_name()] = tu

        # Reparse to initialize the PCH cache even for auto completion
        # This should be done by index.parse(), however it is not.
        # So we need to reparse ourselves.
        tu.reparse([self._file])
        return tu


class SynchronizedTranslationUnitParser(object):
    def __init__(self, editor):
        self._editor = editor
        self._index = clang.cindex.Index.create()
        self._translation_units = dict()
        self._up_to_date = set()
        self._synchronized_doers = {}
        self._doer_lock = SynchronizedDoer()

    def translation_unit_do(self, file, function):
        def do_it():
            return self._call_if_not_null(function, self._parse(file))
        return self._file_synchronized_do(file, do_it)

    def translation_unit_if_parsed_do(self, file, function):
        doer = self._synchronized_doer_for_file_named(file[0])

        def do_it():
            if file[0] in self._up_to_date:
                return self._call_if_not_null(function, self._parse(file))
        try:
            return doer.do_if_not_locked(do_it)
        except AlreadyLocked:
            pass

    def is_parsed_or_parsing(self, file_name):
        return self._is_parsed(file_name) or self._is_parsing(file_name)

    def _synchronized_doer_for_file_named(self, file_name):
        def do_it():
            try:
                return self._synchronized_doers[file_name]
            except KeyError:
                doer = SynchronizedDoer()
                self._synchronized_doers[file_name] = doer
                return doer
        return self._doer_lock.do(do_it)

    def _file_synchronized_do(self, file, action):
        doer = self._synchronized_doer_for_file_named(file[0])
        return doer.do(action)

    def _call_if_not_null(self, function, arg):
        if arg:
            return function(arg)

    def _parse(self, file):
        self._editor.display_message("[" + threading.currentThread().name + " ] - Starting parse: " + file[0])
        action = TranslationUnitParsingAction(self._editor, self._index, self._translation_units, self._up_to_date, file)
        result = action.parse()
        self._editor.display_message("[" + threading.currentThread().name + " ] - Finished parse: " + file[0])
        return result

    def clear_caches(self):
        self._up_to_date.clear()

    def _is_parsed(self, file_name):
        return file_name in self._up_to_date

    def _is_parsing(self, file_name):
        doer = self._synchronized_doer_for_file_named(file_name)
        return doer.is_locked()


class IdleTranslationUnitParserThreadDistributor():
    def __init__(self, editor, translation_unit_parser):
        self._editor = editor
        self._remaining_files = Queue.PriorityQueue()
        self._parser = translation_unit_parser
        self._threads = [IdleTranslationUnitParserThread(self._editor, translation_unit_parser, self._remaining_files, self.enqueue_file) for i in range(1, 8)]
        for thread in self._threads:
            thread.start()

    def terminate(self):
        for thread in self._threads:
            thread.terminate()
        # Only start waking up threads after all threads know they must terminate on notification
        for thread in self._threads:
            self._remaining_files.put((-1, None))

    def enqueue_file(self, file, high_priority=True):
        if self._parser.is_parsed_or_parsing(file[0]):
            return
        if high_priority:
            priority = 0
        else:
            priority = 1
        if (priority, file) not in self._remaining_files.queue:
            self._remaining_files.put((priority, file))


class IdleTranslationUnitParserThread(threading.Thread):
    def __init__(self, editor, translation_unit_parser, _remaining_files, enqueue_in_any_thread):
        threading.Thread.__init__(self)
        self._editor = editor
        self._parser = translation_unit_parser
        self._enqueue_in_any_thread = enqueue_in_any_thread
        self._remaining_files = _remaining_files
        self._termination_requested = False

    def run(self):
        try:
            while True:
                ignored_priority, current_file = self._remaining_files.get()
                if self._termination_requested:
                    return
                self._parser.translation_unit_do(current_file, self._enqueue_related_files)
                self._remaining_files.task_done()
        except Exception, e:
            self._editor.display_message("Exception thrown in idle thread: " + str(e))

    def terminate(self):
        self._termination_requested = True

    def _enqueue_related_files(self, translation_unit):
        #This doesn't really add any includes in the preamble.
        #self._enqueue_includes(translation_unit)
        self._enqueue_definition_files(translation_unit)

    def _enqueue_includes(self, translation_unit):
        for include in translation_unit.get_includes():
            file_name = include.source.name
            self._enqueue_in_any_thread(get_file_for_file_name(file_name), high_priority=False)

    def _enqueue_definition_files(self, translation_unit):
        finder = DefinitionFileFinder(self._editor.excluded_directories(), translation_unit.spelling)
        for file_name in finder.definition_files():
            self._enqueue_in_any_thread(get_file_for_file_name(file_name), high_priority=False)


class AlreadyLocked(Exception):
    pass


class SynchronizedDoer(object):
    def __init__(self):
        self._lock = threading.RLock()

    def do(self, action):
        self._lock.acquire()
        try:
            return action()
        finally:
            self._lock.release()

    def do_if_not_locked(self, action):
        if self._lock.acquire(blocking=0):
            try:
                return action()
            finally:
                self._lock.release()
        else:
            raise AlreadyLocked()

    def is_locked(self):
        if self._lock.acquire(blocking=0):
            try:
                return False
            finally:
                self._lock.release()
        else:
            return True


class TranslationUnitAccessor(object):
    def __init__(self, editor):
        self._editor = editor
        self._parser = SynchronizedTranslationUnitParser(self._editor)
        self._idle_translation_unit_parser_thread_distributor = IdleTranslationUnitParserThreadDistributor(self._editor, self._parser)

    def terminate(self):
        self._idle_translation_unit_parser_thread_distributor.terminate()

    def current_translation_unit_do(self, function):
        current_file = self._editor.current_file()
        return self.translation_unit_do(current_file, function)

    def current_translation_unit_if_parsed_do(self, function):
        current_file = self._editor.current_file()
        return self._parser.translation_unit_if_parsed_do(current_file, function)

    def translation_unit_for_file_named_do(self, file_name, function):
        try:
            file = get_file_for_file_name(file_name)
            return self.translation_unit_do(file, function)
        except IOError:
            return None

    def clear_caches(self):
        self._parser.clear_caches()

    def enqueue_translation_unit_creation(self, file):
        self._idle_translation_unit_parser_thread_distributor.enqueue_file(file)

    def translation_unit_do(self, file, function):
        return self._parser.translation_unit_do(file, function)


class DiagnosticsHighlighter(object):

    def __init__(self, editor):
        self._editor = editor
        self._highlight_style = "Diagnostic"

    def _highlight_diagnostic(self, diagnostic):

        if diagnostic.severity not in (diagnostic.Warning, diagnostic.Error, diagnostic.Note):
            return

        single_location_range = ExportedRange(diagnostic.location, diagnostic.location)
        self._editor.highlight_range(single_location_range, self._highlight_style)

        # Use this wired kind of iterator as the python clang libraries
                    # have a bug in the range iterator that stops us to use:
                    #
                    # | for range in diagnostic.ranges
                    #
        for i in range(len(diagnostic.ranges)):
            range_i = diagnostic.ranges[i]
            self._editor.highlight_range(range_i, self._highlight_style)

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
            type = 'W'
        elif diagnostic.severity == diagnostic.Error:
            type = 'E'
        elif diagnostic.severity == diagnostic.Fatal:
            type = 'E'
        else:
            type = 'O'

        return dict({ 'filename' : file_name,
          'lnum' : diagnostic.location.line,
          'col' : diagnostic.location.column,
          'text' : diagnostic.spelling,
          'type' : type})

    def get_quick_fix_list(self, tu):
        return filter (None, map (self._get_quick_fix, tu.diagnostics))


class Completer(object):

    def __init__(self, editor, translation_unit_accessor, complete_flags):
        self._editor = editor
        self._translation_unit_accessor = translation_unit_accessor
        self._complete_flags = complete_flags

    def format_results(self, result):
        completion = dict()

        abbr = self.get_abbr(result.string)

        word = filter(lambda x: not x.isKindInformative() and not x.isKindResultType(), result.string)
        args_pos = []
        cur_pos = 0
        for chunk in word:
            chunk_len = len(chunk.spelling)
            if chunk.isKindPlaceHolder():
                args_pos += [[ cur_pos, cur_pos + chunk_len ]]
            cur_pos += chunk_len

        word = "".join(map(lambda x: x.spelling, word))

        completion['word'] = word
        completion['abbr'] = abbr
        completion['menu'] = word
        completion['info'] = word
        completion['args_pos'] = args_pos
        completion['dup'] = 0

        # Replace the number that represents a specific kind with a better
        # textual representation.
        completion['kind'] = kinds[result.cursorKind]

        return completion

    def get_current_completions(self, base):

        sort_by_priority = self._editor.sort_algorithm() == 'priority'

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
            self.result = self.get_current_completion_results(self._line, self._column)
        except Exception, e:
            self._editor.display_message("Exception thrown in completion thread: " + str(e))
        finally:
            CompleteThread.lock.release()

    def get_current_completion_results(self, line, column):
        def _do_it(translation_unit):
            return translation_unit.codeComplete(
                self._file_name, line, column, [self._current_file], self._complete_flags)

        return self._translation_unit_accessor.translation_unit_do(self._current_file, _do_it)


class DeclarationFinder(object):

    def __init__(self, editor, translation_unit_accessor):
        self._editor = editor
        self._translation_unit_accessor = translation_unit_accessor

    def _get_current_cursor_in_translation_unit(self, translation_unit):
        location = self._editor.current_location().clang_location(translation_unit)
        return translation_unit.getCursor(location)

    def _find_declaration_in_translation_unit(self, translation_unit):
        current_location_cursor = self._get_current_cursor_in_translation_unit(translation_unit)
        parent_cursor = current_location_cursor.get_semantic_parent()
        if parent_cursor == clang.cindex.Cursor.nullCursor():
            return current_location_cursor.get_cursor_referenced()
        for child_cursor in parent_cursor.get_children():
            if child_cursor.get_canonical() == current_location_cursor.get_canonical():
                return child_cursor
        return current_location_cursor.get_cursor_referenced()

    def _declaration_cursors_do(self, function):
        def call_function_with_declaration_in(translation_unit):
            declaration_cursor = self._find_declaration_in_translation_unit(translation_unit)
            if declaration_cursor:
                function(declaration_cursor)

        self._translation_unit_accessor.current_translation_unit_do(call_function_with_declaration_in)

    def declaration_locations_do(self, function):
        self._declaration_cursors_do(lambda cursor: function(cursor.extent.start))


class NoDefinitionFound(Exception):
    pass


def get_definition_or_reference(cursor):
    definition = cursor.get_definition()
    if definition:
        return definition
    else:
        return cursor.get_cursor_referenced()


class DefinitionFinder(object):

    def __init__(self, editor, translation_unit_accessor):
        self._editor = editor
        self._translation_unit_accessor = translation_unit_accessor

    def _find_corresponding_cursor_in_alternate_translation_unit(self, cursor, other_translation_unit):
        file = cursor.extent.start.file
        other_file = other_translation_unit.getFile(file.name)
        for offset in range(cursor.extent.start.offset, cursor.extent.end.offset + 1):
            location = other_translation_unit.getLocationForOffset(other_file, offset)
            cursor_at_location = other_translation_unit.getCursor(location)
            if cursor_at_location.get_usr() == cursor.get_usr():
                return cursor_at_location
        return None

    def _corresponding_cursors_in_any_alternate_translation_unit_do(self, cursor, function):
        def call_function_with_alternate_cursor(translation_unit):
            alternate_cursor = self._find_corresponding_cursor_in_alternate_translation_unit(cursor, translation_unit)
            if alternate_cursor:
                function(alternate_cursor)
        for file_name in self._alternate_files(cursor.extent.start.file.name):
            self._translation_unit_accessor.translation_unit_for_file_named_do(file_name, call_function_with_alternate_cursor)

    def _find_definition_in_translation_unit(self, translation_unit, location):
        cursor = translation_unit.getCursor(location)
        if cursor.kind.is_unexposed:
            self._editor.display_message("Item at current location is not exposed. Cursor kind: " + str(cursor.kind))
        return get_definition_or_reference(cursor)

    def _definition_or_declaration_cursor_of_current_cursor_in(self, translation_unit):
        current_location = self._editor.current_location().clang_location(translation_unit)
        return self._find_definition_in_translation_unit(translation_unit, current_location)

    def _alternate_files(self, file_name):
        finder = DefinitionFileFinder(self._editor.excluded_directories(), file_name)
        return finder.definition_files()

    def _guessed_alternate_translation_units_do(self, file_name, function):
        for file in self._alternate_files(file_name):
            self._translation_unit_accessor.translation_unit_for_file_named_do(file, function)

    def _definitions_of_current_cursor_do(self, translation_unit, function):
        def call_function_with_definition_if_exists(cursor):
            definition = cursor.get_definition()
            if definition:
                function(definition)

        definition_or_declaration_cursor = self._definition_or_declaration_cursor_of_current_cursor_in(translation_unit)
        if definition_or_declaration_cursor:
            if not definition_or_declaration_cursor.is_definition():
                self._corresponding_cursors_in_any_alternate_translation_unit_do(
                  definition_or_declaration_cursor,
                  call_function_with_definition_if_exists)
            function(definition_or_declaration_cursor)

    def _definition_cursors_do(self, function):
        for translation_unit_do in [
            self._translation_unit_accessor.current_translation_unit_do,
            lambda f: self._guessed_alternate_translation_units_do(self._editor.file_name(), f),
            ]:
            translation_unit_do(lambda translation_unit: self._definitions_of_current_cursor_do(translation_unit, function))

    def definition_locations_do(self, function):
        self._definition_cursors_do(lambda cursor: function(cursor.extent.start))


class DefinitionFileFinder(object):
    """
    Given the name of a file (e.g. foo.h), finds similarly named files (e.g. foo.cpp,
    fooI.cpp) somewhere nearby in the file system.
    """
    def __init__(self, excluded_directories, target_file_name):
        self._excluded_directories = excluded_directories
        self._target_file_name = target_file_name
        self._split_target = os.path.splitext(os.path.basename(self._target_file_name))
        self._visited_directories = set()
        self._search_limit = 50
        self._num_directories_searched = 0

    def definition_files(self):
        directory_name = os.path.dirname(self._target_file_name)
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
        self._num_directories_searched = 1 + self._num_directories_searched
        if self._num_directories_searched > self._search_limit:
            return
        self._visited_directories.add(os.path.abspath(directory_name))
        try:
            for file_name in os.listdir(directory_name):
                absolute_name = os.path.abspath(os.path.join(directory_name, file_name))
                if os.path.isdir(absolute_name) and file_name not in self._excluded_directories:
                    if absolute_name not in self._visited_directories:
                        for result in self._search_directory_and_subdirectories(absolute_name):
                            yield result
                else:
                    if self._is_definition_file_name(file_name):
                        yield absolute_name
        except OSError:
            pass

    def _ratio(self, a, b):
        return Levenshtein.ratio(a, b)

    def _is_definition_file_name(self, file_name):
        split_file_name = os.path.splitext(file_name)
        return (self._ratio(split_file_name[0], self._split_target[0]) > 0.8 and
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
