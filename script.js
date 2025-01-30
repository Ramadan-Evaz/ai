// داده‌های بازی‌ها
const matchesData = [
  {
    id: 1,
    teams: "تیم الف vs تیم ب",
    date: "۱۴۰۴/۰۱/۱۰",
    time: "۲۱:۰۰",
    description: "بازی افتتاحیه بین تیم الف و تیم ب. انتظار می‌رود بازی جذاب و پرتماشاگری باشد.",
    liveStreamLink: "https://www.youtube.com/embed/dQw4w9WgXcQ"
  },
  {
    id: 2,
    teams: "تیم ج vs تیم د",
    date: "۱۴۰۴/۰۱/۱۱",
    time: "۲۲:۳۰",
    description: "بازی حساس مرحله مقدماتی با حضور بازیکنان سرشناس هر دو تیم.",
    liveStreamLink: "https://www.aparat.com/v/xxxxx" // مثال آپارات
  },
  {
    id: 3,
    teams: "تیم ه vs تیم و",
    date: "۱۴۰۴/۰۱/۱۲",
    time: "۲۳:۰۰",
    description: "مصاف تماشایی بین تیم ه و تیم و که هر دو مدعی قهرمانی هستند.",
    liveStreamLink: "https://www.youtube.com/embed/dQw4w9WgXcQ"
  },
  {
    id: 4,
    teams: "تیم ز vs تیم ی",
    date: "۱۴۰۴/۰۱/۱۳",
    time: "۲۱:۳۰",
    description: "این مسابقه یکی از مهم‌ترین بازی‌های مرحله یک‌چهارم نهایی محسوب می‌شود.",
    liveStreamLink: "https://www.youtube.com/embed/dQw4w9WgXcQ"
  }
];

/**
 * بارگذاری کارت‌های بازی در سکشن #matches-container
 */
function loadMatches() {
  const container = document.getElementById("matches-container");
  if (!container) return;

  container.innerHTML = ""; // در صورت نیاز پاکسازی اولیه

  matchesData.forEach(match => {
    // ساخت یک کارت
    const card = document.createElement("div");
    card.className = "match-card";

    // محتوای کارت
    card.innerHTML = `
      <h3>${match.teams}</h3>
      <p><strong>تاریخ:</strong> ${match.date}</p>
      <p><strong>ساعت:</strong> ${match.time}</p>
      <a href="javascript:void(0)" class="btn-match-details">جزئیات</a>
    `;

    // رویداد کلیک برای دکمه جزئیات
    const detailsBtn = card.querySelector(".btn-match-details");
    detailsBtn.addEventListener("click", () => {
      showMatchDetails(match.id);
    });

    container.appendChild(card);
  });
}

/**
 * نمایش اطلاعات بازی در یک مودال (همراه با پخش زنده)
 * @param {number} matchId 
 */
function showMatchDetails(matchId) {
  const match = matchesData.find(m => m.id === matchId);
  if (!match) return;

  // عنصر والد محتوای مودال
  const matchDetailsEl = document.getElementById("match-details");

  // پر کردن اطلاعات
  matchDetailsEl.innerHTML = `
    <h2>${match.teams}</h2>
    <p><strong>تاریخ:</strong> ${match.date}</p>
    <p><strong>ساعت:</strong> ${match.time}</p>
    <p>${match.description}</p>
    <h3>پخش زنده</h3>
    <iframe 
      src="${match.liveStreamLink}" 
      width="100%" 
      height="360" 
      allowfullscreen>
    </iframe>
  `;

  // نمایش مودال
  const modal = document.getElementById("match-modal");
  modal.style.display = "block";
}

/**
 * بستن مودال
 */
function closeModal() {
  const modal = document.getElementById("match-modal");
  modal.style.display = "none";
  // برای پاک کردن محتوای iframe (و جلوگیری از ادامه پخش) در صورت تمایل:
  document.getElementById("match-details").innerHTML = "";
}

/**
 * شمارش معکوس تا تاریخ/زمان مشخص
 * @param {string} dateString - به فرمت "YYYY-MM-DDTHH:MM:SS" یا مشابه
 */
function startCountdown(dateString) {
  const countdownElement = document.getElementById("countdown-timer");
  if (!countdownElement) return;

  const endDate = new Date(dateString).getTime();

  const interval = setInterval(() => {
    const now = new Date().getTime();
    const distance = endDate - now;

    if (distance < 0) {
      clearInterval(interval);
      countdownElement.innerHTML = "<p>بازی شروع شده است!</p>";
      return;
    }

    const days = Math.floor(distance / (1000 * 60 * 60 * 24));
    const hours = Math.floor((distance % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
    const minutes = Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60));
    const seconds = Math.floor((distance % (1000 * 60)) / 1000);

    document.getElementById("days").textContent = days;
    document.getElementById("hours").textContent = hours;
    document.getElementById("minutes").textContent = minutes;
    document.getElementById("seconds").textContent = seconds;
  }, 1000);
}

// بستن مودال با کلیک روی ضربدر
document.addEventListener("DOMContentLoaded", () => {
  const closeBtn = document.getElementById("close-modal");
  if (closeBtn) {
    closeBtn.addEventListener("click", closeModal);
  }
  // اگر می‌خواهید با کلیک روی پس‌زمینه مودال هم بسته شود:
  const modal = document.getElementById("match-modal");
  if (modal) {
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        closeModal();
      }
    });
  }
});
