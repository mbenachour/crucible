/* Planted bug c-obo-1: off-by-one write past a fixed buffer. */
#include <stddef.h>

void copy_name(const char *src, size_t len, char *dst /* dst has `len` bytes */) {
    size_t i;
    /* BUG(c-obo-1): condition should be i < len; i <= len writes dst[len]. */
    for (i = 0; i <= len; i++) {
        dst[i] = src[i];
    }
}
