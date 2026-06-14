<template>
  <div class="ui-master-detail">
    <aside class="ui-master-detail__master">
      <slot name="master" />
    </aside>
    <section class="ui-master-detail__detail">
      <slot name="detail" />
    </section>
  </div>
</template>
