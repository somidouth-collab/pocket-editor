#include <iostream>

struct Counter {
  int value = 0;
  void increment() { ++value; }
};

int main() {
  Counter counter;
  counter.increment();
  std::cout << "Counter: " << counter.value << '\n';
  return 0;
}
