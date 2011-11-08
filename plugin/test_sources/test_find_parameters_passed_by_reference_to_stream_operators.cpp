class Foo
{
public:
  Foo operator >> (Foo &foo)
  {
    return *this;
  }
};


void test()
{
  Foo foo;

  foo >> foo;
}
