class Foo
{
public:
  void non_virtual_method();
  virtual void virtual_method();
};

void Foo::virtual_method()
{
}

void Foo::non_virtual_method()
{
}
