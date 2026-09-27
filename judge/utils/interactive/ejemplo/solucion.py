# Solución de referencia: búsqueda binaria. flush=True en cada print.
import sys

n = int(input())
lo, hi = 1, n
while lo < hi:
    mid = (lo + hi) // 2
    print('?', mid, flush=True)
    r = input().strip()
    if r == '-1':
        sys.exit(0)
    if r == '=':
        lo = hi = mid
        break
    if r == '>':
        lo = mid + 1
    else:
        hi = mid - 1
print('!', lo, flush=True)
