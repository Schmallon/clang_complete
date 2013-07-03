import unittest
import actions
from test_environment import translation_unit_for, assert_ranges_equal


class TestActions(unittest.TestCase):

    def assert_function_finds_marked_ranges(self, function, source):
        with translation_unit_for(source) as translation_unit:
            assert_ranges_equal(
                self,
                source,
                function(translation_unit))

    def test_find_virtual_method_calls(self):
        self.assert_function_finds_marked_ranges(
            actions.find_virtual_method_declarations,
            """
            class Foo
            {
            public:
              void non_virtual_method();
              virtual void /*START*/virtual_method/*END*/();
            };


            void test()
            {
              Foo foo;

              foo.virtual_method();
              foo.non_virtual_method();
            }""")

    def test_find_omitted_default_arguments(self):
        self.assert_function_finds_marked_ranges(
            actions.find_omitted_default_arguments,
            """
            void function_with_default_arguments(int x, int y = 0);

            void test()
            {
              /*START*/function_with_default_arguments(5)/*END*/;
              function_with_default_arguments(5, 6);
            }""")

    def test_find_static_method_declarations(self):
        self.maxDiff = None
        self.assert_function_finds_marked_ranges(
            actions.find_static_method_declarations,
            """
            class Foo
            {
            public:
              void non_static_method();
              static void /*START*/static_method/*END*/();
            };

            void Foo::/*START*/static_method/*END*/()
            {
            }

            void Foo::non_static_method()
            {
            }""")

    def test_find_virtual_method_declarations(self):
        self.maxDiff = None
        self.assert_function_finds_marked_ranges(
            actions.find_virtual_method_declarations,
            """
            class Foo
            {
            public:
              void non_virtual_method();
              virtual void /*START*/virtual_method/*END*/();
            };

            void Foo::/*START*/virtual_method/*END*/()
            {
            }

            void Foo::non_virtual_method()
            {
            }""")

    def test_find_member_references(self):
        self.maxDiff = None
        self.assert_function_finds_marked_ranges(
            actions.find_member_references,
            """
            class Other
            {
              public:
                void some_method();
                int x;
            };

            class TestSuper
            {
            public:
              int DefinedInSuper();
            };

            class Test : public TestSuper
            {
            public:
              int reference_member()
              {
                return /*START*/member_being_referenced/*END*/;
              }

              int reference_super_member()
              {
                return /*START*/DefinedInSuper/*END*/();
              }

              int call_method_on_member()
              {
                return /*START*/member/*END*/->call_method_on_member();
              }

              void call_method_on_member_of_field_with_other_class()
              {
                /*START*/mpOther/*END*/->some_method();
              }

              int reference_parameter(int parameter)
              {
                return parameter;
              }

              int reference_member_of_non_this(Test test)
              {
                return test.member_being_referenced;
              }

              int reference_member_using_this()
              {
                return this->member_being_referenced;
              }

              int member_being_referenced;
              Test *member;
              Other *mpOther;
            };""")
