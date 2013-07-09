import threading
import time
from common import ExportedRange, ExportedLocation
import traceback


class VimInterface(object):

    """Abortable perform doesn't yet work. We must stay within one OS-thread.
    TODO: Find some green-thread implementation for python"""
    def user_abortable_perform(self, consumer, producer):
        stop_running = [False]

        def pass_to_consumer_if_not_aborted(result):
            if not stop_running:
                consumer(result)
            stop_running.append(True)

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
                self._logger.display_message(
                    "Warning: Calling vim command %s from different thread: %s" % (command, current_thread.getName()))
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
            'Diagnostic': {'group': 'clang_diagnostic', 'default': 'gui=undercurl guisp=Red'},
            "Non-const reference": {'group': 'clang_non_const_reference', 'default': 'ctermbg=6 guibg=Yellow'},
            "Virtual method call": {'group': 'clang_virtual_method_call', 'default': 'guibg=LightRed'},
            "Virtual method declaration": {'group': 'clang_virtual_method_declaration', 'default': 'guibg=LightRed'},
            "Static method declaration": {'group': 'clang_static_method_declaration', 'default': 'gui=underline'},
            "Member reference": {'group': 'clang_member_reference', 'default': 'gui=bold guifg=#005079 guibg=#DBF2FF'},
            "Referenced Range": {'group': 'clang_referenced_range', 'default': 'gui=bold guifg=#FFFF00 guibg=#0000FF', 'priority': '-10'},
            "Referencing Range": {'group': 'clang_referencing_range', 'default': 'gui=bold guifg=#00FFFF guibg=#FF0000', 'priority': '-5'},
            "Omitted default argument": {'group': 'clang_omitted_default_argument', 'default': 'ctermbg=6 gui=undercurl guisp=DarkCyan'}}

        self._cached_variable_names = ["g:clang_user_options",
                                       "b:clang_user_options",
                                       "b:clang_parameters",
                                       "g:clang_excluded_directories"]
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

    def should_highlight_interesting_ranges(self):
        return int(self._get_uncached_variable("g:clang_highlight_interesting_ranges"))

    def user_options(self):
        user_options_global = self._split_options(
            self._get_variable("g:clang_user_options"))
        user_options_local = self._split_options(
            self._get_variable("b:clang_user_options"))
        parameters_local = self._split_options(
            self._get_variable("b:clang_parameters"))
        return user_options_global + user_options_local + parameters_local

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
        self._vim.command("syntax clear %s" %
                          self._highlight_group_for_id(highlight_style))

    def highlight_range(self, range, highlight_style):
        self.highlight(range.start.line, range.start.column,
                       range.end.line, range.end.column, highlight_style)

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
        self._vim.command("call g:CalledFromPythonClangDisplayQuickFix(" +
                          self._quick_fix_list_to_str(quick_fix_list) + ")")

    def _highlight_group_for_id(self, id):
        return self._id_to_highlight_group[id]["group"]

    def _priority_for_id(self, id):
        try:
            return self._id_to_highlight_group[id]["priority"]
        except KeyError:
            return str(-100)
