<script setup lang="ts">
defineProps<{ spec: Record<string, unknown> }>()
</script>
<template>
  <pre class="raw">{{ JSON.stringify(spec, null, 2) }}</pre>
</template>
<style scoped>
.raw { font-size: 11px; color: var(--text-dim); background: #131620; padding: 10px; border-radius: 8px; overflow-x: auto; }
</style>
