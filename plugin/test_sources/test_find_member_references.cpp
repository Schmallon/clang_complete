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
    return member_being_referenced;
  }

  int reference_super_member()
  {
    return DefinedInSuper();
  }

  int call_method_on_member()
  {
    return member->call_method_on_member();
  }

  void call_method_on_member_of_field_with_other_class()
  {
    mpOther->some_method();
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
};
