void test_find_references_to_outside_of_selection()
{
  int defined_outside_selection = 1;

  for (int i = 0; i < 10; i++)
  {
    int in_selection1 = defined_outside_selection + 2;
    int in_selection2 = defined_outside_selection + in_selection1;
    defined_outside_selection = in_selection2;
  }
}
