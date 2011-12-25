class Foo
{
public:
  static void static_method();
  virtual void virtual_method();
  virtual void virtual_method() const;
  Foo& operator << (Foo&);
};

void Foo::static_method()
{
}

void Foo::virtual_method()
{
}

void Foo::virtual_method() const
{
}

Foo& Foo::operator << (Foo&)
{
  return *this;
}
