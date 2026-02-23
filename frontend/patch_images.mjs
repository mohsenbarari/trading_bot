import fs from 'fs';
const file = './src/components/ChatView.vue';
let content = fs.readFileSync(file, 'utf8');

const targetStr = `              <div class="msg-content-wrapper"
                @touchstart="startLongPress($event, msg)"
                @touchmove="cancelLongPress(); handleTouchMove($event, msg)"
                @touchend="endLongPress($event, msg)"
                :style="getSwipeStyle(msg)"
              >`;

const replacementStr = `              <div class="msg-content-wrapper"
                @touchstart="startLongPress($event, msg)"
                @touchmove="cancelLongPress(); handleTouchMove($event, msg)"
                @touchend="endLongPress($event, msg)"
                :style="getSwipeStyle(msg)"
              >`;

// Wait, the error is missing end tag, let's look at the outer structure.
// Ah, the issue is that earlier I completely missed the closing tag for a `<template>` or `<a href>`?
// Wait.
