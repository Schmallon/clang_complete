class Foo
{
public:
  void non_virtual_method();
  virtual void virtual_method();
};


void test()
{
  Foo foo;

  foo.virtual_method();
  foo.non_virtual_method();
}
