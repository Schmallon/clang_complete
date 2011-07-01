#include "a.h"
#include "b.h"

void c(); //No definition available

void test()
{
  a();
  b();
  c();
}

void a()
{
}
