void function_with_default_arguments(int x, int y = 0);

void test()
{
  function_with_default_arguments(5);
  function_with_default_arguments(5, 6);
}
