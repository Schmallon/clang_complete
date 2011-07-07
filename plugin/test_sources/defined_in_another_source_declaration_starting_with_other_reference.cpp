#include "defined_in_another_source_declaration_starting_with_other_reference.h"

struct_referenced_by_declaration_t function_returning_struct(){
  struct struct_referenced_by_declaration_t result = {1};
  return result;
}

