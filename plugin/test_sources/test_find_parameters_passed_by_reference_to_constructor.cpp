class Foo
{
public:
  Foo(int &x)
  {
    x = 42;
  }
};


void test()
{
  int passed_by_reference;
  Foo foo(passed_by_reference);
}
