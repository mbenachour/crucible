# Minimal build so a Hunter can compile fragments under ASan/UBSan (§9.2).
CC ?= cc
CFLAGS ?= -g -O1 -fsanitize=address,undefined -fno-omit-frame-pointer -Wall -Wextra

SRC := $(wildcard src/*.c)
OBJ := $(SRC:.c=.o)

libfixture.a: $(OBJ)
	ar rcs $@ $^

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f src/*.o libfixture.a
