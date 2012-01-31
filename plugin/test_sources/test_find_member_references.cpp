class AnotherClass
{
  public:
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
  int bar(Test test, AnotherClass parameter)
  {
    int non_member_being_referenced = 42;
    return member_being_referenced +
      DefinedInSuper() +
      test.member_being_referenced +
      non_member_being_referenced +
      parameter.x;
  }

  int member_being_referenced;
};
