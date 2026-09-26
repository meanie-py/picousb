#include "lwip/apps/mdns.h"
#include "lwip/dhcp.h"
#include "lwip/etharp.h"
#include "lwip/init.h"
#include "lwip/netif.h"
#include "lwip/tcp.h"
#include "lwip/timeouts.h"
#include "netif/ethernet.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static uint32_t now, packets;
unsigned sys_arch_protect(void) {
    return 0;
}
void sys_arch_unprotect(unsigned state) {
    (void)state;
}
uint32_t sys_now(void) {
    return now;
}
static err_t output(struct netif *netif, struct pbuf *p) {
    (void)netif;
    (void)p;
    packets++;
    return ERR_OK;
}
static err_t initialize(struct netif *netif) {
    netif->name[0] = 't';
    netif->name[1] = 's';
    netif->mtu = 1500;
    netif->flags = NETIF_FLAG_BROADCAST | NETIF_FLAG_ETHARP | NETIF_FLAG_IGMP;
    netif->hwaddr_len = 6;
    memcpy(netif->hwaddr, "\x02\x00\x00\x00\x00\x01", 6);
    netif->output = etharp_output;
    netif->linkoutput = output;
    return ERR_OK;
}
int main(void) {
    struct netif netif = {0};
    ip4_addr_t address, mask, gateway;
    IP4_ADDR(&address, 192, 0, 2, 2);
    IP4_ADDR(&mask, 255, 255, 255, 0);
    IP4_ADDR(&gateway, 192, 0, 2, 1);
    lwip_init();
    assert(netif_add(&netif, &address, &mask, &gateway, NULL, initialize, ethernet_input));
    netif_set_hostname(&netif, "synthetic-picousb");
    netif_set_default(&netif);
    mdns_resp_init();
    assert(mdns_resp_add_netif(&netif, "synthetic-picousb") == ERR_OK);
    netif_set_up(&netif);
    netif_set_link_up(&netif);
    assert(dhcp_start(&netif) == ERR_OK);
    /* Exercise core timer churn alongside DHCP and mDNS probes/announcements. */
    for (now = 0; now < 120000; now += 10) {
        if (now == 2000) {
            dhcp_stop(&netif);
            netif_set_addr(&netif, &address, &mask, &gateway);
        }
        if (now == 60000) {
            netif_set_link_down(&netif);
            netif_set_link_up(&netif);
        }
        sys_check_timeouts();
    }
    assert(packets > 5);
    assert(mdns_resp_remove_netif(&netif) == ERR_OK);
    dhcp_cleanup(&netif);
    netif_remove(&netif);
    puts("lwIP DHCP/mDNS timer integration passed");
    return 0;
}
