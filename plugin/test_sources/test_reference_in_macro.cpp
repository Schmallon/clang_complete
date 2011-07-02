#define MACRO(x) x();

void used_in_macro()
{
}

void test()
{
  MACRO(used_in_macro)
}
