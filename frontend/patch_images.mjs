import fs from 'fs';
const file = './src/components/ChatView.vue';
let content = fs.readFileSync(file, 'utf8');

// Replace getImageUrl
const oldGetImageUrl = `// Get full image URL
function getImageUrl(path: string) {
  if (!path) return ''
  if (path.startsWith('http')) return path
  return \`\${props.apiBaseUrl}\${path}\`
}`;

const newGetImageUrl = `// Get full image URL
function getImageUrl(content: string) {
  if (!content) return ''
  try {
    if (content.startsWith('{')) {
      const data = JSON.parse(content)
      if (data.file_id) {
        return \`\${props.apiBaseUrl}/api/chat/files/\${data.file_id}?token=\${props.jwtToken}\`
      }
    }
  } catch (e) {}
  if (content.startsWith('http')) return content
  return \`\${props.apiBaseUrl}\${content}\`
}

// Get thumbnail Base64
function getImageThumbnail(content: string) {
  if (!content) return ''
  try {
    if (content.startsWith('{')) {
      const data = JSON.parse(content)
      return data.thumbnail || ''
    }
  } catch (e) {}
  return ''
}`;

content = content.replace(oldGetImageUrl, newGetImageUrl);

// Replace the template Image tag
const oldTemplate = `            <!-- Image -->
            <template v-else-if="msg.message_type === 'image'">
              <a :href="getImageUrl(msg.content)" target="_blank" class="msg-image-link">
                <img :src="getImageUrl(msg.content)" alt="تصویر" class="msg-image" />
              </a>
            </template>`;

const newTemplate = `            <!-- Image -->
            <template v-else-if="msg.message_type === 'image'">
              <a :href="getImageUrl(msg.content)" target="_blank" class="msg-image-link"
                 :style="{ backgroundImage: getImageThumbnail(msg.content) ? \`url(\${getImageThumbnail(msg.content)})\` : 'none', backgroundSize: 'cover' }">
                <img :src="getImageUrl(msg.content)" alt="تصویر" class="msg-image" loading="lazy" />
              </a>
            </template>`;

content = content.replace(oldTemplate, newTemplate);

fs.writeFileSync(file, content);
console.log('Patched carefully.');
