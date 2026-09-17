<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
const isLogin = computed(() => route.path === '/login')
</script>

<template>
  <div class="wrap" :class="{ login: isLogin }">
    <router-view />
  </div>
</template>

<style scoped>
.wrap { height: 100%; }
</style>
