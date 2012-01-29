class Foo
{
public:
  void non_static_method();
  static void static_method();
};

void Foo::static_method()
{
}

void Foo::non_static_method()
{
}
