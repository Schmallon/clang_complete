void foo(int by_value, int& by_reference, const int& by_const_reference)
{
  by_reference = 42;
}

void test()
{
  int by_value = 0;
  int by_reference = 1;
  int by_const_reference = 1;

  foo(by_value, by_reference, by_const_reference);
}
