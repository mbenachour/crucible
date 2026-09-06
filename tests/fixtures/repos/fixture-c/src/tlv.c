/* Planted bug c-oob-1: attacker-controlled length used as memcpy size. */
#include <stdint.h>
#include <string.h>

/* Wire format: [1 byte type][2 byte length][payload]. */
int tlv_parse(const uint8_t *in, size_t in_len, uint8_t *out, size_t out_cap) {
    if (in_len < 3)
        return -1;

    uint16_t claimed_len = (uint16_t)((in[1] << 8) | in[2]);

    /* BUG(c-oob-1): claimed_len is never checked against in_len or out_cap
       before it is used as the copy size. */
    memcpy(out, in + 3, claimed_len);
    (void)out_cap;
    return (int)claimed_len;
}
