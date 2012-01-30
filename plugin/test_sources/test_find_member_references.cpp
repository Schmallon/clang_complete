class AnotherClass
{
  public:
    int x;
};

class Foo
{
  int bar(AnotherClass parameter)
  {
    int non_member_being_referenced = 42;
    return member_being_referenced +
      non_member_being_referenced +
      parameter.x;
  }

  int member_being_referenced;
};
