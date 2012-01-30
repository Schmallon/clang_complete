class Foo
{
  int bar()
  {
    int non_member_being_referenced = 42;
    return member_being_referenced + non_member_being_referenced;
  }

  int member_being_referenced;
};
