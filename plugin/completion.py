import threading


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
