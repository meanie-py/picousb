#ifndef DEVBOX_NATIVE_ARCH_H
#define DEVBOX_NATIVE_ARCH_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#define LWIP_RAND() ((uint32_t)rand())
typedef unsigned sys_prot_t;
#endif
